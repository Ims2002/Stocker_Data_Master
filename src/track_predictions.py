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
    target_up_down = 1 si close(date_predicha) > close(sesión de origen)

"Sesión de origen" se toma como la fila que está `horizon` sesiones ANTES
de `date_predicha` en el histórico de `daily_prices` de ESE ticker (mismo
criterio que `feat["close"].shift(-horizon)` en gold.py: N filas antes en
la serie ordenada por fecha, no "N días naturales antes"). `horizon` se
deduce del propio `model_version` (`predict.horizon_from_model_version`,
2026-08-13, ver CONTEXTO.md "Horizontes de predicción: semana y mes") —
para horizonte 1 (el único que existía antes de esa fecha), la sesión de
origen es justo la fila inmediatamente anterior, que es la fecha que
`predict.py` usó como "hoy" al generar la predicción
(`date_predicha = nth_trading_day(gold_inference.date, horizon)`), así
que el resultado coincide con lo que `gold.py` habría calculado si esa
fecha hubiera sido parte del histórico de entrenamiento. Para horizontes
más largos generaliza el mismo criterio: compara contra la sesión en la
que se hizo realmente la predicción, no contra la sesión inmediatamente
anterior a `date_predicha`.

Empates (`close(date_predicha) == close(sesión de origen)`) cuentan como
"baja/igual" (target_up_down=0), igual que en gold.py.

Revisión de auditoría (16/09/2026):
- C2: solo se resuelven fechas cuya sesión ya ha cerrado. Antes, con el
  pipeline corriendo a media sesión, se resolvía con un cierre parcial y,
  como las filas resueltas no se vuelven a tocar, el error quedaba para
  siempre (507 de 4.544 resultados incorrectos).
- M3: el resultado se calcula con `adj_close`, igual que el nuevo target de
  gold.py.
- M5: si la predicción guarda `date_origen`, se compara contra esa fecha
  exacta; si no (predicciones antiguas), contra la fila `horizon` sesiones
  antes, como hasta ahora.
- `--reresolve` vuelve a calcular TODAS las predicciones resueltas (usar una
  vez tras corregir los datos con `load.py --refresh-all`).

Uso:
    python track_predictions.py
    python track_predictions.py --reresolve

Pensado para ejecutarse a diario, después de `load.py` (o en el mismo
cron/tarea programada), para que las predicciones vayan quedando
resueltas según se actualiza `daily_prices`. Idempotente: una predicción
ya resuelta no se vuelve a tocar (solo se leen las que tienen
`actual_target_up_down IS NULL`).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod  # noqa: E402
import market_time  # noqa: E402
from predict import horizon_from_model_version  # noqa: E402

# Precio con el que se decide "subió / no subió" (mismo que gold.TARGET_PRICE_COLUMN).
PRICE_COLUMN = "adj_close"


def read_pending_predictions(engine: Engine) -> pd.DataFrame:
    """Predicciones sin resolver todavía (`actual_target_up_down IS
    NULL`). Puede haber varias filas por (ticker, date_predicha) si hay
    más de un `model_version` en juego — cada una se resuelve por
    separado, todas comparten el mismo resultado real."""
    query = select(
        dbmod.predictions.c.ticker,
        dbmod.predictions.c.date_predicha,
        dbmod.predictions.c.model_version,
        dbmod.predictions.c.date_origen,
    ).where(dbmod.predictions.c.actual_target_up_down.is_(None))
    with engine.begin() as conn:
        result = conn.execute(query)
        rows = result.fetchall()
        columns = result.keys()
    return pd.DataFrame(rows, columns=columns)


def read_closes_for_tickers(tickers: list[str], engine: Engine) -> pd.DataFrame:
    """Histórico de cierres ajustados (`ticker`, `date`, `close`) para los
    tickers dados, ordenado por ticker y fecha ascendente — lo mínimo
    necesario para resolver predicciones, no todo `daily_prices`. La
    columna se llama `close` por compatibilidad, pero contiene
    PRICE_COLUMN."""
    if not tickers:
        return pd.DataFrame(columns=["ticker", "date", "close"])
    query = (
        select(
            dbmod.daily_prices.c.ticker,
            dbmod.daily_prices.c.date,
            dbmod.daily_prices.c[PRICE_COLUMN].label("close"),
        )
        .where(dbmod.daily_prices.c.ticker.in_(tickers))
        .order_by(dbmod.daily_prices.c.ticker, dbmod.daily_prices.c.date)
    )
    with engine.begin() as conn:
        result = conn.execute(query)
        rows = result.fetchall()
        columns = result.keys()
    return pd.DataFrame(rows, columns=columns)


def resolve_predictions(pending: pd.DataFrame, closes: pd.DataFrame, now=None) -> pd.DataFrame:
    """Cruza `pending` con `closes` y devuelve solo las filas que ya se
    pueden resolver (con `actual_target_up_down` calculado), listas para
    actualizar en `predictions`. Las que aún no tienen el cierre real
    disponible se omiten del resultado — se reintentarán en la próxima
    ejecución, sin ningún estado especial que gestionar."""
    if pending.empty or closes.empty:
        return pending.iloc[0:0].assign(actual_target_up_down=pd.Series(dtype="int"))

    last_closed = market_time.last_closed_session(now=now)
    resolved_rows = []
    for ticker, group in closes.groupby("ticker", sort=False):
        series = group.set_index("date")["close"].sort_index()
        ticker_pending = pending[pending["ticker"] == ticker]
        for _, row in ticker_pending.iterrows():
            date_predicha = row["date_predicha"]
            if date_predicha not in series.index:
                continue  # el cierre de ese día todavía no está en daily_prices
            if date_predicha > last_closed:
                continue  # sesión en curso: el precio guardado no es definitivo (C2)
            pos = series.index.get_loc(date_predicha)
            date_origen = row.get("date_origen")
            if date_origen is not None and not pd.isna(date_origen):
                if date_origen not in series.index:
                    continue  # falta el cierre de la sesión de origen
                close_origin = series.loc[date_origen]
            else:
                horizon = horizon_from_model_version(row["model_version"])
                origin_pos = pos - horizon
                if origin_pos < 0:
                    continue  # no hay suficiente histórico anterior para este horizonte/ticker
                close_origin = series.iloc[origin_pos]
            close_today = series.iloc[pos]
            actual = int(close_today > close_origin)
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


def reset_resolved(engine: Engine) -> int:
    """Vuelve a poner a NULL todos los resultados reales ya calculados, para
    recalcularlos con los datos corregidos."""
    with engine.begin() as conn:
        result = conn.execute(
            dbmod.predictions.update()
            .where(dbmod.predictions.c.actual_target_up_down.is_not(None))
            .values(actual_target_up_down=None)
        )
    return result.rowcount or 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reresolve", action="store_true", help="recalcula también las predicciones ya resueltas")
    args = parser.parse_args(argv)

    engine = dbmod.get_engine()
    dbmod.init_db(engine)

    if args.reresolve:
        n_reset = reset_resolved(engine)
        print(f"[track_predictions] {n_reset} resultado(s) reales reiniciados para recalcular.", file=sys.stderr)

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
        f"Quedan {still_pending} pendientes (sesión aún sin cerrar o sin datos en daily_prices).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
