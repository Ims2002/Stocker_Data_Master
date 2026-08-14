"""
src/gold.py — construcción de gold_train y gold_inference desde daily_prices.

Por cada ticker, separa físicamente (nunca en una sola tabla, ver
CONTEXTO.md):

- `gold_train`: todas las fechas salvo la última, con `close_next_day` y
  `target_up_down` ya calculados — la variable objetivo se construye
  desplazando el `Close` (no `adj_close`) un día hacia atrás, tal como fija
  CONTEXTO.md.
- `gold_inference`: SOLO la última fecha disponible, solo features, sin
  ninguna columna de label — ni siquiera vacía.

Ambas tablas se recalculan por completo para cada ticker que se procesa
(delete + insert), no de forma incremental: así no quedan filas
desactualizadas si cambia la lógica de features/limpieza más adelante, y
a este volumen (~2.500 filas/ticker) el coste es insignificante.

Uso:
    python gold.py                # todos los tickers de config.TICKERS
    python gold.py AAPL MSFT       # tickers concretos
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod  # noqa: E402
from config import MIN_HISTORY_ROWS_FOR_GOLD, PREDICTION_HORIZONS, TICKERS  # noqa: E402
from features import ROLLING_FEATURE_COLUMNS, attach_news_features, compute_features  # noqa: E402

# Nombre de columna (close_Nd, target_up_down_Nd) por horizonte (sesiones
# de mercado) — horizonte 1 usa los nombres históricos sin sufijo
# (close_next_day/target_up_down) para no romper el esquema/dashboards ya
# existentes; 5 y 20 son las columnas nuevas (ver CONTEXTO.md, "Horizontes
# de predicción: semana y mes", 2026-08-13).
HORIZON_COLUMNS = {
    horizon: (
        ("close_next_day", "target_up_down") if horizon == 1
        else (f"close_{horizon}d", f"target_up_down_{horizon}d")
    )
    for horizon in PREDICTION_HORIZONS
}

# Columnas de features que va a llevar gold_train/gold_inference (crudas +
# derivadas por features.py) — no incluye ticker/date, que se añaden
# aparte. DEBE mantenerse en el mismo orden que `db._gold_feature_columns()`
# (ver CONTEXTO.md, "Ampliación de features", 2026-08-06 y "Preparación de
# noticias como feature", 2026-08-08).
_GOLD_FEATURE_COLUMNS = [
    "open", "high", "low", "close", "adj_close", "volume",
    "return_1d", "ma_5", "ma_10", "ma_20", "volatility_10d",
    "return_5d", "return_20d", "rsi_14", "macd_line", "macd_signal",
    "price_std_20", "volume_ma_10", "day_of_week",
    "news_sentiment_3d", "news_volume_3d",
]


def read_daily_prices(ticker: str, engine: Engine) -> pd.DataFrame:
    """Lee todo el histórico de `daily_prices` de un ticker, ordenado por
    fecha ascendente. Usa `result.keys()` (no una lista de columnas a
    mano) para los nombres de columna, así que sigue funcionando aunque
    cambie el esquema de `daily_prices` sin tocar este módulo."""
    query = (
        dbmod.daily_prices.select()
        .where(dbmod.daily_prices.c.ticker == ticker)
        .order_by(dbmod.daily_prices.c.date)
    )
    with engine.begin() as conn:
        result = conn.execute(query)
        rows = result.fetchall()
        columns = result.keys()
    return pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame()


def read_news_sentiment(ticker: str, engine: Engine) -> pd.DataFrame:
    """Lee `news_sentiment_daily` (VISTA, ver `db.py`) para un ticker.

    Es una vista creada con SQL crudo (no hay `Table()` de SQLAlchemy
    para ella, a diferencia de `daily_prices`), así que se consulta con
    `text()` — y por eso `date` vuelve como texto, no como
    `datetime.date` (mismo caveat ya documentado en
    `news.pending_work_items()`). Se convierte explícitamente con pandas
    antes de devolver, para que el tipo coincida con `daily_prices.date`
    al hacer el merge en `features.attach_news_features()`.

    Devuelve un DataFrame vacío si el ticker todavía no tiene ninguna
    fila en `news_articles` — normal mientras el backfill de noticias
    sigue en marcha (ver CONTEXTO.md, backfill de 6 meses en curso)."""
    query = text(
        "SELECT date, avg_sentiment_score, n_articles "
        "FROM news_sentiment_daily WHERE ticker = :ticker ORDER BY date"
    )
    with engine.begin() as conn:
        rows = conn.execute(query, {"ticker": ticker}).fetchall()
    df = pd.DataFrame(rows, columns=["date", "avg_sentiment_score", "n_articles"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def build_gold_frames(
    raw: pd.DataFrame, ticker: str, news: pd.DataFrame | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A partir del histórico crudo (daily_prices) de un ticker, calcula
    features y target, y separa en (train_df, inference_df) — ver
    docstring del módulo.

    `news` (opcional, ver `read_news_sentiment()`): si no se pasa o el
    ticker no tiene ninguna fila todavía, `attach_news_features()` deja
    `news_sentiment_3d`/`news_volume_3d` en su valor neutro (0, tras el
    calentamiento) en vez de fallar.

    Puede devolver train_df e inference_df vacíos si no hay histórico
    suficiente (ver MIN_HISTORY_ROWS_FOR_GOLD).
    """
    if len(raw) < MIN_HISTORY_ROWS_FOR_GOLD:
        return pd.DataFrame(), pd.DataFrame()

    feat = compute_features(raw)
    feat = attach_news_features(feat, news)

    # Targets por horizonte: shift(-N sesiones) sobre Close (no adj_close),
    # tal como fija CONTEXTO.md para el horizonte de 1 sesión, generalizado
    # a 5/20 (ver CONTEXTO.md, "Horizontes de predicción: semana y mes",
    # 2026-08-13). IMPORTANTE: una comparación normal `(close_nd > close)`
    # con NaN a la izquierda devuelve False, no NaN (comportamiento de
    # numpy) — sin el `.where()` de abajo, las últimas N-1 filas de cada
    # ticker (dentro de las que sí entran en train, ver `is_last` más
    # abajo) quedarían mal etiquetadas como "baja" en vez de excluidas.
    for horizon, (close_col, target_col) in HORIZON_COLUMNS.items():
        feat[close_col] = feat["close"].shift(-horizon)
        raw_target = (feat[close_col] > feat["close"]).astype("float")
        feat[target_col] = raw_target.where(feat[close_col].notna())

    complete = feat[ROLLING_FEATURE_COLUMNS].notna().all(axis=1)
    last_date = feat["date"].max()
    is_last = feat["date"] == last_date

    train_df = feat[(~is_last) & complete & feat["target_up_down"].notna()].copy()
    train_df["target_up_down"] = train_df["target_up_down"].astype(int)
    train_df["ticker"] = ticker
    # NaN -> None explícito SOLO en las columnas nullable de horizonte
    # largo (close_5d/target_up_down_5d/close_20d/target_up_down_20d):
    # `to_dict(orient="records")` deja NaN tal cual para columnas float,
    # y sqlite no debe recibir NaN en una columna con CHECK ... IN (0, 1)
    # — necesita NULL de verdad. Se hace columna a columna (no con un
    # `.astype(object).where(...)` sobre todo el DataFrame) para no tocar
    # el dtype de las demás columnas (features, ticker, date) sin motivo.
    for horizon, (close_col, target_col) in HORIZON_COLUMNS.items():
        if horizon == 1:
            continue
        close_mask = train_df[close_col].notna()
        train_df[close_col] = train_df[close_col].astype(object).where(close_mask, None)
        target_mask = train_df[target_col].notna()
        # Int64 (nullable) primero para que los valores presentes se
        # inserten como enteros de verdad (0/1), no como 0.0/1.0 — la
        # columna en gold_train es Integer, no Float.
        train_df[target_col] = train_df[target_col].astype("Int64").astype(object).where(target_mask, None)

    inference_df = feat[is_last & complete].copy()
    inference_df["ticker"] = ticker

    horizon_cols = [col for pair in HORIZON_COLUMNS.values() for col in pair]
    train_cols = ["ticker", "date", *_GOLD_FEATURE_COLUMNS, *horizon_cols]
    inference_cols = ["ticker", "date", *_GOLD_FEATURE_COLUMNS]

    return train_df[train_cols], inference_df[inference_cols]


def upsert_gold_for_ticker(ticker: str, train_df: pd.DataFrame, inference_df: pd.DataFrame, engine: Engine) -> None:
    """Reemplaza por completo las filas de `ticker` en gold_train y
    gold_inference (delete + insert), dentro de una única transacción."""
    with engine.begin() as conn:
        conn.execute(dbmod.gold_train.delete().where(dbmod.gold_train.c.ticker == ticker))
        conn.execute(dbmod.gold_inference.delete().where(dbmod.gold_inference.c.ticker == ticker))

        if not train_df.empty:
            conn.execute(dbmod.gold_train.insert(), train_df.to_dict(orient="records"))
        if not inference_df.empty:
            conn.execute(dbmod.gold_inference.insert(), inference_df.to_dict(orient="records"))


def build_gold_for_ticker(ticker: str, engine: Engine | None = None) -> tuple[int, int]:
    """Construye y persiste gold_train/gold_inference para un ticker.
    Devuelve (filas_train, filas_inference)."""
    engine = engine or dbmod.get_engine()
    raw = read_daily_prices(ticker, engine)

    if raw.empty:
        raise ValueError(f"No hay datos en daily_prices para {ticker}. Ejecuta antes load.py.")
    if len(raw) < MIN_HISTORY_ROWS_FOR_GOLD:
        raise ValueError(
            f"Histórico insuficiente para {ticker}: {len(raw)} filas "
            f"(mínimo {MIN_HISTORY_ROWS_FOR_GOLD})."
        )

    news = read_news_sentiment(ticker, engine)
    train_df, inference_df = build_gold_frames(raw, ticker, news)
    upsert_gold_for_ticker(ticker, train_df, inference_df, engine)
    return len(train_df), len(inference_df)


def main(tickers: list[str] | None = None) -> int:
    """Procesa cada ticker de forma aislada — un fallo no aborta el resto
    del lote (mismo criterio que download.py/load.py)."""
    engine = dbmod.get_engine()
    dbmod.init_db(engine)

    failed: list[str] = []
    for ticker in (tickers or TICKERS):
        try:
            n_train, n_inf = build_gold_for_ticker(ticker, engine)
            print(f"[gold] {ticker}: {n_train} fila(s) en gold_train, {n_inf} en gold_inference", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"[gold] {ticker}: DESCARTADO ({exc})", file=sys.stderr)
            failed.append(ticker)

    if failed:
        print(f"[gold] {len(failed)} ticker(s) fallaron: {failed}", file=sys.stderr)
    return len(failed)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or None))
