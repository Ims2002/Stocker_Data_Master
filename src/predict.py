"""
src/predict.py — inferencia sobre gold_inference y persistencia en predictions.

Carga el modelo entrenado más reciente (o uno concreto por model_version),
predice sobre `gold_inference` (una fila por ticker: el día más reciente
disponible, sin ninguna columna de label — ver CONTEXTO.md) y guarda cada
predicción en la tabla `predictions`, obligatoria y no opcional: cada
predicción real queda registrada con su `model_version`, para poder medir
más adelante el rendimiento real del modelo en producción frente al
backtest (cruzando `predictions.actual_target_up_down` con el cierre real
en `daily_prices` una vez se conoce).

`date_predicha` es la sesión de mercado siguiente a la fecha de
`gold_inference` (no simplemente "+1 día natural": se calcula con el
calendario de mercado para no predecir sobre un sábado o festivo).

Horizontes (2026-08-13, ver CONTEXTO.md "Horizontes de predicción: semana
y mes"): además del horizonte a 1 sesión (por defecto), hay modelos a 5 y
20 sesiones si se han entrenado con `model.py --horizon 5/20`. `--horizon`
elige cuál usar cuando no se pasa `--model-version` explícito.

Uso:
    python predict.py                              # horizonte 1 (día), modelo más reciente
    python predict.py --horizon 5                   # horizonte semana, modelo más reciente de ese horizonte
    python predict.py --model-version rf_h1_2026...  # usa un modelo concreto
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

import joblib
import pandas as pd
import pandas_market_calendars as mcal
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod  # noqa: E402
from config import DEFAULT_PREDICTION_HORIZON, MARKET_CALENDAR, MODELS_DIR, PREDICTION_HORIZONS  # noqa: E402
from model import build_feature_matrix  # noqa: E402

# Marcador de horizonte en el nombre del modelo (ver model.save_model,
# 2026-08-13, CONTEXTO.md "Horizontes de predicción: semana y mes"):
# "random_forest_h5_20260813172000" -> horizonte 5. Los modelos guardados
# ANTES de este cambio no llevan "_hN_" en el nombre — se tratan como
# horizonte 1 por convención (es lo único que existía entonces).
_HORIZON_MARKER_RE = re.compile(r"_h(\d+)_")


def horizon_from_model_version(model_version: str) -> int:
    match = _HORIZON_MARKER_RE.search(model_version)
    return int(match.group(1)) if match else DEFAULT_PREDICTION_HORIZON

def read_gold_inference(engine: Engine) -> pd.DataFrame:
    """Usa `result.keys()` (no una lista de columnas a mano) para los
    nombres de columna — igual que `model.read_gold_train`,
    `gold.read_daily_prices` y `dashboard/data_access.get_gold_train_for_ticker`
    (ver CONTEXTO.md, "Ampliación de features", 2026-08-06). Antes de este
    cambio, esta lista se había quedado con el esquema de 13 columnas de
    antes de esa ampliación — un bug real que habría asignado mal los
    nombres de las columnas nuevas de gold_inference en la próxima
    ejecución."""
    with engine.begin() as conn:
        result = conn.execute(dbmod.gold_inference.select())
        rows = result.fetchall()
        columns = result.keys()
    return pd.DataFrame(rows, columns=columns)


def latest_model_path(horizon: int = DEFAULT_PREDICTION_HORIZON) -> Path:
    """Modelo más reciente para un horizonte concreto (1/5/20 sesiones,
    ver `config.PREDICTION_HORIZONS`) — antes de que existieran varios
    horizontes, `MODELS_DIR` solo tenía modelos a 1 sesión, así que
    `horizon_from_model_version` trata cualquier archivo sin `_hN_` en el
    nombre como horizonte 1 (ver CONTEXTO.md, 'Horizontes de predicción:
    semana y mes'). Ordena por nombre (que termina en timestamp), igual
    que antes — sigue sin distinguir de forma robusta entre model_type
    distintos con el mismo horizonte si sus timestamps son muy próximos,
    limitación preexistente que no cambia con esto."""
    candidates = sorted(MODELS_DIR.glob("*.joblib"))
    matching = [c for c in candidates if horizon_from_model_version(c.stem) == horizon]
    if not matching:
        raise FileNotFoundError(
            f"No hay ningún modelo para el horizonte {horizon} ({PREDICTION_HORIZONS.get(horizon, '?')}) "
            f"en {MODELS_DIR}. Ejecuta antes `python model.py --horizon {horizon}`."
        )
    return matching[-1]


def model_path_for_version(model_version: str) -> Path:
    path = MODELS_DIR / f"{model_version}.joblib"
    if not path.exists():
        raise FileNotFoundError(f"No existe el modelo {model_version} en {MODELS_DIR}.")
    return path


def next_trading_day(after_date: dt.date, calendar_name: str = MARKET_CALENDAR) -> dt.date:
    """Primera sesión de mercado después de `after_date` (nunca fin de
    semana/festivo), usando el mismo calendario que load.py."""
    return nth_trading_day(after_date, 1, calendar_name)


def nth_trading_day(after_date: dt.date, n: int, calendar_name: str = MARKET_CALENDAR) -> dt.date:
    """Sesión de mercado que cae exactamente `n` sesiones después de
    `after_date` (nunca fin de semana/festivo) — generaliza
    `next_trading_day` (n=1) a los horizontes largos (2026-08-13, ver
    CONTEXTO.md 'Horizontes de predicción: semana y mes'). El rango de
    calendario pedido a `mcal` tiene margen de sobra (varios festivos
    seguidos no deberían nunca hacer falta más de ~1.5 días naturales por
    sesión de mercado, aquí se piden 3 por seguridad)."""
    cal = mcal.get_calendar(calendar_name)
    schedule = cal.schedule(
        start_date=after_date + dt.timedelta(days=1),
        end_date=after_date + dt.timedelta(days=max(14, n * 3)),
    )
    if len(schedule) < n:
        raise RuntimeError(f"No se encontraron {n} sesión(es) de mercado tras {after_date}")
    return schedule.index[n - 1].date()


def predict_all(gold_inf: pd.DataFrame, model, model_version: str, horizon: int = DEFAULT_PREDICTION_HORIZON) -> pd.DataFrame:
    """Genera una predicción por fila de gold_inference. Devuelve un
    DataFrame listo para upsert en `predictions`.

    `date_predicha` es la sesión de mercado que cae `horizon` sesiones
    después de la fecha de `gold_inference` (1 = mañana, 5 = ~1 semana
    vista, 20 = ~1 mes vista) — no la sesión inmediatamente siguiente
    salvo que `horizon=1`. `track_predictions.py` necesita este mismo
    `horizon` (lo recupera del propio `model_version`) para saber contra
    qué sesión de origen comparar el cierre real."""
    x = build_feature_matrix(gold_inf)
    proba_up = model.predict_proba(x)[:, 1]
    pred_label = (proba_up >= 0.5).astype(int)

    now = dt.datetime.now()
    out = pd.DataFrame({
        "ticker": gold_inf["ticker"],
        "date_predicha": [nth_trading_day(d, horizon) for d in gold_inf["date"]],
        "predicted_target_up_down": pred_label,
        "predicted_probability": proba_up,
        "model_version": model_version,
        "predicted_at": now,
        "actual_target_up_down": None,
    })
    return out


def upsert_predictions(df: pd.DataFrame, engine: Engine) -> int:
    if df.empty:
        return 0
    with engine.begin() as conn:
        for record in df.to_dict(orient="records"):
            stmt = sqlite_insert(dbmod.predictions).values(**record)
            update_cols = {
                c: stmt.excluded[c]
                for c in record
                if c not in ("ticker", "date_predicha", "model_version")
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker", "date_predicha", "model_version"],
                set_=update_cols,
            )
            conn.execute(stmt)
    return len(df)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-version", help="model_version concreto (por defecto, el más reciente del horizonte elegido)")
    parser.add_argument(
        "--horizon", type=int, choices=sorted(PREDICTION_HORIZONS), default=DEFAULT_PREDICTION_HORIZON,
        help="sesiones de mercado vista: 1=día (por defecto), 5=semana, 20=mes — ignorado si se pasa "
        "--model-version (el horizonte se deduce del propio nombre del modelo)",
    )
    args = parser.parse_args(argv)

    engine = dbmod.get_engine()
    dbmod.init_db(engine)

    gold_inf = read_gold_inference(engine)
    if gold_inf.empty:
        print("[predict] gold_inference está vacío. Ejecuta antes gold.py.", file=sys.stderr)
        return 1

    if args.model_version:
        path = model_path_for_version(args.model_version)
        model_version = args.model_version
        horizon = horizon_from_model_version(model_version)
    else:
        path = latest_model_path(horizon=args.horizon)
        model_version = path.stem
        horizon = args.horizon

    bundle = joblib.load(path)
    model = bundle["model"]

    preds = predict_all(gold_inf, model, model_version, horizon=horizon)
    n = upsert_predictions(preds, engine)
    print(
        f"[predict] {n} predicción(es) guardadas (model_version={model_version}, horizonte={horizon})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
