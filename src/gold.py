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
from sqlalchemy.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod  # noqa: E402
from config import MIN_HISTORY_ROWS_FOR_GOLD, TICKERS  # noqa: E402
from features import ROLLING_FEATURE_COLUMNS, compute_features  # noqa: E402

# Columnas de features que va a llevar gold_train/gold_inference (crudas +
# derivadas por features.py) — no incluye ticker/date, que se añaden
# aparte. DEBE mantenerse en el mismo orden que `db._gold_feature_columns()`
# (ver CONTEXTO.md, "Ampliación de features", 2026-08-06).
_GOLD_FEATURE_COLUMNS = [
    "open", "high", "low", "close", "adj_close", "volume",
    "return_1d", "ma_5", "ma_10", "ma_20", "volatility_10d",
    "return_5d", "return_20d", "rsi_14", "macd_line", "macd_signal",
    "price_std_20", "volume_ma_10", "day_of_week",
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


def build_gold_frames(raw: pd.DataFrame, ticker: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A partir del histórico crudo (daily_prices) de un ticker, calcula
    features y target, y separa en (train_df, inference_df) — ver
    docstring del módulo.

    Puede devolver train_df e inference_df vacíos si no hay histórico
    suficiente (ver MIN_HISTORY_ROWS_FOR_GOLD).
    """
    if len(raw) < MIN_HISTORY_ROWS_FOR_GOLD:
        return pd.DataFrame(), pd.DataFrame()

    feat = compute_features(raw)

    # Target: shift(-1) sobre Close (no adj_close), tal como fija CONTEXTO.md.
    feat["close_next_day"] = feat["close"].shift(-1)
    feat["target_up_down"] = (feat["close_next_day"] > feat["close"]).astype("float")

    complete = feat[ROLLING_FEATURE_COLUMNS].notna().all(axis=1)
    last_date = feat["date"].max()
    is_last = feat["date"] == last_date

    train_df = feat[(~is_last) & complete & feat["target_up_down"].notna()].copy()
    train_df["target_up_down"] = train_df["target_up_down"].astype(int)
    train_df["ticker"] = ticker

    inference_df = feat[is_last & complete].copy()
    inference_df["ticker"] = ticker

    train_cols = ["ticker", "date", *_GOLD_FEATURE_COLUMNS, "close_next_day", "target_up_down"]
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

    train_df, inference_df = build_gold_frames(raw, ticker)
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
