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

Uso:
    python predict.py                              # usa el modelo más reciente en MODELS_DIR
    python predict.py --model-version rf_2026...    # usa un modelo concreto
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import joblib
import pandas as pd
import pandas_market_calendars as mcal
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod  # noqa: E402
from config import MARKET_CALENDAR, MODELS_DIR  # noqa: E402
from model import build_feature_matrix  # noqa: E402

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


def latest_model_path() -> Path:
    candidates = sorted(MODELS_DIR.glob("*.joblib"))
    if not candidates:
        raise FileNotFoundError(f"No hay ningún modelo en {MODELS_DIR}. Ejecuta antes model.py.")
    return candidates[-1]


def model_path_for_version(model_version: str) -> Path:
    path = MODELS_DIR / f"{model_version}.joblib"
    if not path.exists():
        raise FileNotFoundError(f"No existe el modelo {model_version} en {MODELS_DIR}.")
    return path


def next_trading_day(after_date: dt.date, calendar_name: str = MARKET_CALENDAR) -> dt.date:
    """Primera sesión de mercado después de `after_date` (nunca fin de
    semana/festivo), usando el mismo calendario que load.py."""
    cal = mcal.get_calendar(calendar_name)
    schedule = cal.schedule(
        start_date=after_date + dt.timedelta(days=1),
        end_date=after_date + dt.timedelta(days=14),
    )
    if schedule.empty:
        raise RuntimeError(f"No se encontró la siguiente sesión de mercado tras {after_date}")
    return schedule.index[0].date()


def predict_all(gold_inf: pd.DataFrame, model, model_version: str) -> pd.DataFrame:
    """Genera una predicción por fila de gold_inference. Devuelve un
    DataFrame listo para upsert en `predictions`."""
    x = build_feature_matrix(gold_inf)
    proba_up = model.predict_proba(x)[:, 1]
    pred_label = (proba_up >= 0.5).astype(int)

    now = dt.datetime.now()
    out = pd.DataFrame({
        "ticker": gold_inf["ticker"],
        "date_predicha": [next_trading_day(d) for d in gold_inf["date"]],
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
    parser.add_argument("--model-version", help="model_version concreto (por defecto, el más reciente)")
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
    else:
        path = latest_model_path()
        model_version = path.stem

    bundle = joblib.load(path)
    model = bundle["model"]

    preds = predict_all(gold_inf, model, model_version)
    n = upsert_predictions(preds, engine)
    print(f"[predict] {n} predicción(es) guardadas (model_version={model_version})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
