"""
src/track_predictions.py — resuelve predicciones reales con el cierre real.

Cierra el círculo del "Contrato de evaluación" (ver CONTEXTO.md): cada
predicción real se guarda en `predictions` en el momento de predecir
(`predict.py`), pero sin resultado — el cierre de `date_predicha` todavía
no existe. Este módulo recorre las predicciones con
`actual_target_up_down IS NULL` y, para cada una, comprueba si
`daily_prices` ya tiene el cierre de `date_predicha`. Si lo tiene, calcula
el resultado real y actualiza la fila; si no, la deja igual (se
reintentará la próxima vez que se ejecute).

Cálculo del resultado, EXACTAMENTE igual que `gold.py` (nunca
recalculado con una fórmula distinta a mano):
    target_up_down = 1 si close(date_predicha) > close(sesión anterior)

"Sesión anterior" se toma como la fila inmediatamente anterior en el
histórico de `daily_prices` de ESE ticker (mismo criterio que
`feat["close"].shift(-1)` en gold.py: la fila anterior en la serie
ordenada por fecha, no "el día natural de antes"). En operación normal,
esa fila anterior es justo la fecha que `predict.py` usó como "hoy" al
generar la predicción (`date_predicha = next_trading_day(gold_inference.
date)`), así que el resultado coincide con lo que `gold.py` habría
calculado si esa fecha hubiera sido parte del histórico de entrenamiento.

Empates (`close(date_predicha) == close(sesión anterior)`) cuentan como
"baja/igual" (target_up_down=0), igual que en gold.py.

Uso:
    python track_predictions.py

Pensado para ejecutarse a diario, después de `load.py` (o en el mismo
cron/tarea programada), para que las predicciones vayan quedando
resueltas según se actualiza `daily_prices`. Idempotente: una predicción
ya resuelta no se vuelve a tocar (solo se leen las que tienen
`actual_target_up_down IS NULL`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod  # noqa: E402


def read_pending_predictions(engine: Engine) -> pd.DataFrame:
    """Predicciones sin resolver todavía (`actual_target_up_down IS
    NULL`). Puede haber varias filas por (ticker, date_predicha) si hay
    más de un `model_version` en juego — cada una se resuelve por
    separado, todas comparten el mismo resultado real."""
    query = select(
        dbmod.predictions.c.ticker,
        dbmod.predictions.c.date_predicha,
        dbmod.predictions.c.model_version,
    ).where(dbmod.predictions.c.actual_target_up_down.is_(None))
    with engine.begin() as conn:
        result = conn.execute(query)
        rows = result.fetchall()
        columns = result.keys()
    return pd.DataFrame(rows, columns=columns)


def read_closes_for_tickers(tickers: list[str], engine: Engine) -> pd.DataFrame:
    """Histórico de cierres (`ticker`, `date`, `close`) para los tickers
    dados, ordenado por ticker y fecha ascendente — lo mínimo necesario
    para resolver predicciones, no todo `daily_prices`."""
    if not tickers:
        return pd.DataFrame(columns=["ticker", "date", "close"])
    query = (
        select(dbmod.daily_prices.c.ticker, dbmod.daily_prices.c.date, dbmod.daily_prices.c.close)
        .where(dbmod.daily_prices.c.ticker.in_(tickers))
        .order_by(dbmod.daily_prices.c.ticker, dbmod.daily_prices.c.date)
    )
    with engine.begin() as conn:
        result = conn.execute(query)
        rows = result.fetchall()
        columns = result.keys()
    return pd.DataFrame(rows, columns=columns)


def resolve_predictions(pending: pd.DataFrame, closes: pd.DataFrame) -> pd.DataFrame:
    """Cruza `pending` con `closes` y devuelve solo las filas que ya se
    pueden resolver (con `actual_target_up_down` calculado), listas para
    actualizar en `predictions`. Las que aún no tienen el cierre real
    disponible se omiten del resultado — se reintentarán en la próxima
    ejecución, sin ningún estado especial que gestionar."""
    if pending.empty or closes.empty:
        return pending.iloc[0:0].assign(actual_target_up_down=pd.Series(dtype="int"))

    resolved_rows = []
    for ticker, group in closes.groupby("ticker", sort=False):
        series = group.set_index("date")["close"].sort_index()
        ticker_pending = pending[pending["ticker"] == ticker]
        for _, row in ticker_pending.iterrows():
            date_predicha = row["date_predicha"]
            if date_predicha not in series.index:
                continue  # el cierre de ese día todavía no está en daily_prices
            pos = series.index.get_loc(date_predicha)
            if pos == 0:
                continue  # no hay ninguna sesión anterior registrada para este ticker
            close_today = series.iloc[pos]
            close_prev = series.iloc[pos - 1]
            actual = int(close_today > close_prev)
            resolved_rows.append(
                {
                    "ticker": ticker,
                    "date_predicha": date_predicha,
                    "model_version": row["model_version"],
                    "actual_target_up_down": actual,
                }
            )
    return pd.DataFrame(resolved_rows, columns=["ticker", "date_predicha", "model_version", "actual_target_up_down"])


def update_actuals(resolved: pd.DataFrame, engine: Engine) -> int:
    if resolved.empty:
        return 0
    with engine.begin() as conn:
        for record in resolved.to_dict(orient="records"):
            stmt = (
                dbmod.predictions.update()
                .where(dbmod.predictions.c.ticker == record["ticker"])
                .where(dbmod.predictions.c.date_predicha == record["date_predicha"])
                .where(dbmod.predictions.c.model_version == record["model_version"])
                .values(actual_target_up_down=record["actual_target_up_down"])
            )
            conn.execute(stmt)
    return len(resolved)


def main(argv: list[str] | None = None) -> int:
    engine = dbmod.get_engine()
    dbmod.init_db(engine)

    pending = read_pending_predictions(engine)
    if pending.empty:
        print("[track_predictions] no hay predicciones pendientes de resolver.", file=sys.stderr)
        return 0

    tickers = pending["ticker"].unique().tolist()
    closes = read_closes_for_tickers(tickers, engine)
    resolved = resolve_predictions(pending, closes)
    n = update_actuals(resolved, engine)

    still_pending = len(pending) - n
    print(
        f"[track_predictions] {n} predicción(es) resuelta(s) con el cierre real. "
        f"Quedan {still_pending} pendientes (esperando a que daily_prices tenga su fecha).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
