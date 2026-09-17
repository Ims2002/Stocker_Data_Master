"""
src/load.py — carga del CSV crudo (raw) a la tabla `daily_prices` (processed).

Implementa las reglas de limpieza ya decididas en CONTEXTO.md:

- No se rellenan huecos de fin de semana/festivos de mercado; se usa
  `pandas_market_calendars` (calendario NYSE, ver config.MARKET_CALENDAR)
  para distinguirlos de huecos por fallo real de descarga, que se
  registran como aviso (no se auto-rellenan ni se descartan solos).
- Fechas ISO (YYYY-MM-DD), sin componente horaria.
- Duplicados resueltos vía upsert por (ticker, date).
- Registros inválidos (Open/Close nulos o negativos, Volume negativo o
  nulo) se descartan y quedan en un log de calidad
  (data/processed/quality_log.csv).
- La capa processed solo materializa las columnas ya cerradas en el
  esquema de `daily_prices` (open, high, low, close, adj_close, volume).
  Dividends/Stock Splits, que yfinance también devuelve, quedan en el CSV
  raw (auditoría completa) pero no se cargan aquí — no forman parte del
  diseño ya cerrado de esa tabla.

Este módulo no llama a la API directamente: solo lee lo que `download.py`
ya volcó en `data/raw/`, para no saltarse la copia de auditoría "qué se
descargó y cuándo" que exige el pipeline (API → CSV crudo → SQL).

Revisión de auditoría (16/09/2026):
- C2: se descartan las filas de sesiones que todavía no han cerrado
  (`market_time.session_closed`). Antes, ejecutar el pipeline con la bolsa
  abierta guardaba precios de media sesión como cierres definitivos.
- C3: si el CSV trae un split o dividendo, o si las fechas que se solapan
  con lo ya guardado no coinciden, se vuelve a descargar y se sustituye el
  histórico completo del ticker (`refresh_full_history`). yfinance reajusta
  todo el histórico tras esos eventos y la descarga diaria solo trae los
  últimos días.

Uso:
    python load.py                # carga el CSV raw más reciente de cada ticker de config.TICKERS
    python load.py AAPL MSFT      # carga solo los tickers indicados
    python load.py --file data/raw/aapl_20260727.csv --ticker AAPL
    python load.py --refresh APH  # re-descarga y sustituye el histórico completo de APH
    python load.py --refresh-all  # lo mismo para todos los tickers (recarga completa, ~10 min)
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import pandas as pd
import pandas_market_calendars as mcal
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod  # noqa: E402
import market_time  # noqa: E402
from config import CORPORATE_ACTION_TOLERANCE, DOWNLOAD_DELAY_SECONDS, MARKET_CALENDAR, RAW_DATA_DIR, TICKERS  # noqa: E402

QUALITY_LOG_PATH = RAW_DATA_DIR.parent / "processed" / "quality_log.csv"

# Columnas que devuelve yfinance (auto_adjust=False) → nombres del esquema.
# Dividends/Stock Splits se descartan aquí a propósito (ver docstring).
_SCHEMA_COLUMNS = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}


def latest_raw_csv(ticker: str) -> Path:
    """Devuelve el CSV raw más reciente para `ticker` en data/raw/
    (nombre esperado: {ticker}_{YYYYMMDD}.csv, ver download.py)."""
    candidates = sorted(RAW_DATA_DIR.glob(f"{ticker.lower()}_*.csv"))
    if not candidates:
        raise FileNotFoundError(
            f"No hay ningún CSV raw para {ticker} en {RAW_DATA_DIR}. "
            f"Ejecuta antes download.py."
        )
    return candidates[-1]


def read_raw_csv(path: Path) -> pd.DataFrame:
    """Lee el CSV tal como lo escribió download.py (índice = fecha,
    columnas = las que devuelve yfinance)."""
    return pd.read_csv(path, index_col=0, parse_dates=True)


def to_processed_frame(df: pd.DataFrame, ticker: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convierte el DataFrame crudo de yfinance al esquema de `daily_prices`.

    Devuelve (df_limpio, df_descartados); df_descartados incluye el motivo
    del descarte, para el log de calidad.
    """
    missing = [c for c in _SCHEMA_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Faltan columnas esperadas de yfinance: {missing}. "
            f"¿Se descargó con auto_adjust=False?"
        )

    out = df[list(_SCHEMA_COLUMNS)].rename(columns=_SCHEMA_COLUMNS).copy()

    # Fechas ISO, sin componente horaria. Se fuerza el parseo aquí (en vez
    # de fiarse de parse_dates de read_csv): según la versión de pandas/
    # yfinance, el índice leído del CSV puede llegar como DatetimeIndex
    # tz-aware, DatetimeIndex naive, o como texto plano si el parseo
    # automático de read_csv no lo detectó (p. ej. por el offset de tz en
    # el string, "-04:00").
    idx = pd.to_datetime(df.index, utc=True)
    out["date"] = idx.tz_convert(None).normalize().date
    out["ticker"] = ticker

    # Registros inválidos: Open/Close nulos o <=0, Volume nulo o negativo.
    invalid_mask = (
        out["open"].isna()
        | (out["open"] <= 0)
        | out["close"].isna()
        | (out["close"] <= 0)
        | out["volume"].isna()
        | (out["volume"] < 0)
    )

    discarded = out[invalid_mask].copy()
    if not discarded.empty:
        discarded["motivo"] = "open/close nulo o <=0, o volume negativo/nulo"

    cleaned = out.loc[~invalid_mask, ["ticker", "date", "open", "high", "low", "close", "adj_close", "volume"]].copy()
    return cleaned, discarded


def drop_unclosed_sessions(df: pd.DataFrame, ticker: str, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Quita las filas posteriores a la última sesión cerrada (C2): con la
    bolsa abierta, yfinance devuelve la sesión en curso con precio y volumen
    parciales."""
    if df.empty:
        return df
    last_closed = market_time.last_closed_session(now=now)
    mask = df["date"] > last_closed
    if mask.any():
        print(
            f"[load] {ticker}: descartada(s) {int(mask.sum())} sesión(es) aún sin cerrar "
            f"{sorted(df.loc[mask, 'date'])} — se cargarán en la próxima ejecución tras el cierre.",
            file=sys.stderr,
        )
        df = df[~mask]
    return df


def corporate_action_reason(raw: pd.DataFrame, cleaned: pd.DataFrame, ticker: str, engine: Engine) -> str | None:
    """Motivo para recargar el histórico completo de `ticker`, o None (C3).

    - Si ya había filas guardadas para alguna de las fechas descargadas, se
      comparan: un `close` o `adj_close` distinto (más de
      CORPORATE_ACTION_TOLERANCE) significa que yfinance reajustó el
      histórico por un split o dividendo. Basta con esto: tras recargar, las
      fechas vuelven a coincidir y no se repite la recarga los días
      siguientes aunque el evento siga dentro de la ventana.
    - Si no hay solape (primera carga o muchos días sin ejecutar), se mira
      si el CSV trae un split o dividendo (`Stock Splits` / `Dividends`)."""
    if cleaned.empty:
        return None
    dates = list(cleaned["date"])
    with engine.begin() as conn:
        rows = conn.execute(
            select(dbmod.daily_prices.c.date, dbmod.daily_prices.c.close, dbmod.daily_prices.c.adj_close)
            .where(dbmod.daily_prices.c.ticker == ticker)
            .where(dbmod.daily_prices.c.date.in_(dates))
        ).fetchall()

    if rows:
        stored = pd.DataFrame(rows, columns=["date", "close_db", "adj_close_db"])
        merged = cleaned.merge(stored, on="date", how="inner")
        for col in ("close", "adj_close"):
            rel = (merged[col] / merged[f"{col}_db"] - 1).abs()
            if (rel > CORPORATE_ACTION_TOLERANCE).any():
                worst = merged.loc[rel.idxmax(), "date"]
                return f"{col} guardado no coincide con yfinance (p. ej. {worst}, {rel.max():.2%})"
        return None

    has_history = False
    with engine.begin() as conn:
        has_history = conn.execute(
            select(dbmod.daily_prices.c.date).where(dbmod.daily_prices.c.ticker == ticker).limit(1)
        ).first() is not None
    if has_history:
        for col, label in (("Stock Splits", "split"), ("Dividends", "dividendo")):
            if col in raw.columns and (pd.to_numeric(raw[col], errors="coerce").fillna(0) != 0).any():
                return f"{label} en la ventana descargada, sin fechas solapadas para comprobarlo"
    return None


def check_market_calendar_gaps(df: pd.DataFrame, ticker: str) -> list[dt.date]:
    """Compara las fechas cargadas contra el calendario de mercado
    (config.MARKET_CALENDAR) en el mismo rango: devuelve las sesiones de
    mercado esperadas que NO están en `df` — huecos por fallo real de
    descarga, no fines de semana/festivos (que el propio calendario ya
    excluye de "esperadas"). Solo avisa; no rellena ni descarta nada."""
    if df.empty:
        return []
    cal = mcal.get_calendar(MARKET_CALENDAR)
    schedule = cal.schedule(start_date=df["date"].min(), end_date=df["date"].max())
    expected = set(schedule.index.date)
    actual = set(df["date"])
    missing = sorted(expected - actual)
    if missing:
        preview = missing[:5]
        print(
            f"[load] AVISO {ticker}: {len(missing)} sesión(es) de mercado "
            f"esperadas sin datos (posible fallo de descarga, no festivo): "
            f"{preview}{' ...' if len(missing) > 5 else ''}",
            file=sys.stderr,
        )
    return missing


def log_quality_issues(discarded: pd.DataFrame, source_file: Path) -> None:
    if discarded.empty:
        return
    QUALITY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    discarded = discarded.copy()
    discarded["source_file"] = str(source_file)
    discarded["logged_at"] = dt.datetime.now().isoformat()
    header = not QUALITY_LOG_PATH.exists()
    discarded.to_csv(QUALITY_LOG_PATH, mode="a", header=header, index=False)
    print(f"[load] {len(discarded)} fila(s) descartada(s) → {QUALITY_LOG_PATH}", file=sys.stderr)


def upsert_daily_prices(df: pd.DataFrame, engine: Engine) -> int:
    """Upsert por (ticker, date) en daily_prices (INSERT ... ON CONFLICT
    DO UPDATE — resuelve duplicados sin duplicar filas). Una única sentencia
    con todos los registros (executemany), no una por fila."""
    if df.empty:
        return 0

    records = []
    for record in df.to_dict(orient="records"):
        record = dict(record)
        record["volume"] = int(record["volume"])
        for col in ("open", "high", "low", "close", "adj_close"):
            record[col] = None if pd.isna(record[col]) else float(record[col])
        records.append(record)

    stmt = sqlite_insert(dbmod.daily_prices)
    update_cols = {c: stmt.excluded[c] for c in ("open", "high", "low", "close", "adj_close", "volume")}
    stmt = stmt.on_conflict_do_update(index_elements=["ticker", "date"], set_=update_cols)
    with engine.begin() as conn:
        conn.execute(stmt, records)
    return len(records)


def refresh_full_history(ticker: str, engine: Engine | None = None) -> int:
    """Vuelve a descargar el histórico completo (config.HISTORY_PERIOD) de
    `ticker` y SUSTITUYE sus filas de daily_prices (C3). Se borran antes sus
    filas de gold_train/gold_inference (tienen FK a daily_prices y se
    regeneran en `gold.py`). Devuelve el nº de filas cargadas."""
    import download

    engine = engine or dbmod.get_engine()
    raw = download.download_ticker_with_retry(ticker)
    path = download.save_raw_csv(raw, ticker)
    cleaned, discarded = to_processed_frame(raw, ticker)
    cleaned = drop_unclosed_sessions(cleaned, ticker)
    log_quality_issues(discarded, path)
    if cleaned.empty:
        raise RuntimeError(f"la recarga completa de {ticker} no devolvió filas válidas")

    with engine.begin() as conn:
        conn.execute(dbmod.gold_train.delete().where(dbmod.gold_train.c.ticker == ticker))
        conn.execute(dbmod.gold_inference.delete().where(dbmod.gold_inference.c.ticker == ticker))
        conn.execute(dbmod.daily_prices.delete().where(dbmod.daily_prices.c.ticker == ticker))
    n = upsert_daily_prices(cleaned, engine)
    print(f"[load] {ticker}: histórico completo sustituido ({n} filas desde {path.name})", file=sys.stderr)
    return n


def load_ticker(
    ticker: str, csv_path: Path | None = None, engine: Engine | None = None, auto_refresh: bool = True
) -> int:
    """Carga un ticker completo: lee el CSV raw, limpia, descarta sesiones
    sin cerrar, valida contra el calendario de mercado, registra descartes y
    hace upsert en daily_prices. Si detecta un split/dividendo o datos
    reajustados (y `auto_refresh`), recarga el histórico completo en su
    lugar. Devuelve el nº de filas cargadas."""
    engine = engine or dbmod.get_engine()
    dbmod.init_db(engine)
    # Siembra el ticker concreto que se va a cargar, no solo config.TICKERS
    # — si no, un ticker fuera de la config fallaría por la FK a stocks.
    dbmod.seed_stocks(engine, tickers=[ticker])

    path = csv_path or latest_raw_csv(ticker)
    raw = read_raw_csv(path)
    cleaned, discarded = to_processed_frame(raw, ticker)
    cleaned = drop_unclosed_sessions(cleaned, ticker)

    if auto_refresh:
        reason = corporate_action_reason(raw, cleaned, ticker, engine)
        if reason:
            print(f"[load] {ticker}: {reason} — se recarga el histórico completo", file=sys.stderr)
            return refresh_full_history(ticker, engine)

    log_quality_issues(discarded, path)
    check_market_calendar_gaps(cleaned, ticker)

    n = upsert_daily_prices(cleaned, engine)
    print(f"[load] {ticker}: {n} fila(s) cargada(s) en daily_prices desde {path}", file=sys.stderr)
    return n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tickers", nargs="*", help="Tickers a cargar (por defecto, config.TICKERS)")
    parser.add_argument("--file", type=Path, help="CSV raw concreto a cargar (requiere --ticker)")
    parser.add_argument("--ticker", help="Ticker asociado a --file")
    parser.add_argument("--refresh", nargs="+", metavar="TICKER", help="re-descarga y sustituye el histórico completo")
    parser.add_argument("--refresh-all", action="store_true", help="recarga completa de todos los tickers")
    parser.add_argument(
        "--no-auto-refresh", action="store_true",
        help="no recargar el histórico aunque se detecte un split/dividendo (solo avisa)",
    )
    args = parser.parse_args(argv)

    if args.file:
        if not args.ticker:
            parser.error("--file requiere --ticker")
        load_ticker(args.ticker.upper(), csv_path=args.file, auto_refresh=not args.no_auto_refresh)
        return 0

    engine = dbmod.get_engine()
    failed: list[str] = []

    if args.refresh or args.refresh_all:
        dbmod.init_db(engine)
        targets = TICKERS if args.refresh_all else [t.upper() for t in args.refresh]
        dbmod.seed_stocks(engine, tickers=targets)
        for i, ticker in enumerate(targets):
            try:
                refresh_full_history(ticker, engine)
            except Exception as exc:  # noqa: BLE001
                print(f"[load] {ticker}: recarga completa FALLIDA ({exc})", file=sys.stderr)
                failed.append(ticker)
            if i < len(targets) - 1:
                time.sleep(DOWNLOAD_DELAY_SECONDS)
        if failed:
            print(f"[load] {len(failed)} ticker(s) fallaron: {failed}", file=sys.stderr)
        return len(failed)

    # Cada ticker se procesa de forma aislada: si a uno le falta el CSV raw
    # o falla la carga, se registra y se sigue con el resto del lote (ver
    # download.py — mismo criterio para no tumbar 50 tickers por 1 fallo).
    for ticker in (args.tickers or TICKERS):
        try:
            load_ticker(ticker.upper(), engine=engine, auto_refresh=not args.no_auto_refresh)
        except Exception as exc:  # noqa: BLE001
            print(f"[load] {ticker}: DESCARTADO ({exc})", file=sys.stderr)
            failed.append(ticker)

    if failed:
        print(f"[load] {len(failed)} ticker(s) fallaron: {failed}", file=sys.stderr)
    return len(failed)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
