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

Uso:
    python load.py                # carga el CSV raw más reciente de cada ticker de config.TICKERS
    python load.py AAPL MSFT      # carga solo los tickers indicados
    python load.py --file data/raw/aapl_20260727.csv --ticker AAPL
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pandas_market_calendars as mcal
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod  # noqa: E402
from config import MARKET_CALENDAR, RAW_DATA_DIR, TICKERS  # noqa: E402

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
    DO UPDATE — resuelve duplicados sin duplicar filas)."""
    if df.empty:
        return 0

    records = df.to_dict(orient="records")
    with engine.begin() as conn:
        for record in records:
            record = dict(record)
            record["volume"] = int(record["volume"])
            for col in ("open", "high", "low", "close", "adj_close"):
                record[col] = None if pd.isna(record[col]) else float(record[col])

            stmt = sqlite_insert(dbmod.daily_prices).values(**record)
            update_cols = {c: stmt.excluded[c] for c in record if c not in ("ticker", "date")}
            stmt = stmt.on_conflict_do_update(index_elements=["ticker", "date"], set_=update_cols)
            conn.execute(stmt)
    return len(records)


def load_ticker(ticker: str, csv_path: Path | None = None, engine: Engine | None = None) -> int:
    """Carga un ticker completo: lee el CSV raw, limpia, valida contra el
    calendario de mercado, registra descartes y hace upsert en
    daily_prices. Devuelve el nº de filas cargadas."""
    engine = engine or dbmod.get_engine()
    dbmod.init_db(engine)
    # Siembra el ticker concreto que se va a cargar, no solo config.TICKERS
    # — si no, un ticker fuera de la config fallaría por la FK a stocks.
    dbmod.seed_stocks(engine, tickers=[ticker])

    path = csv_path or latest_raw_csv(ticker)
    raw = read_raw_csv(path)
    cleaned, discarded = to_processed_frame(raw, ticker)

    log_quality_issues(discarded, path)
    check_market_calendar_gaps(cleaned, ticker)

    n = upsert_daily_prices(cleaned, engine)
    print(f"[load] {ticker}: {n} fila(s) cargada(s) en daily_prices desde {path}", file=sys.stderr)
    return n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="*", help="Tickers a cargar (por defecto, config.TICKERS)")
    parser.add_argument("--file", type=Path, help="CSV raw concreto a cargar (requiere --ticker)")
    parser.add_argument("--ticker", help="Ticker asociado a --file")
    args = parser.parse_args(argv)

    if args.file:
        if not args.ticker:
            parser.error("--file requiere --ticker")
        load_ticker(args.ticker.upper(), csv_path=args.file)
        return 0

    # Cada ticker se procesa de forma aislada: si a uno le falta el CSV raw
    # o falla la carga, se registra y se sigue con el resto del lote (ver
    # download.py — mismo criterio para no tumbar 50 tickers por 1 fallo).
    failed: list[str] = []
    for ticker in (args.tickers or TICKERS):
        try:
            load_ticker(ticker.upper())
        except Exception as exc:  # noqa: BLE001
            print(f"[load] {ticker}: DESCARTADO ({exc})", file=sys.stderr)
            failed.append(ticker)

    if failed:
        print(f"[load] {len(failed)} ticker(s) fallaron: {failed}", file=sys.stderr)
    return len(failed)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
