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

Revisión de auditoría (16/09/2026):
- A1: `--all-horizons` genera día, semana y mes en una sola ejecución (el
  pipeline diario solo generaba el horizonte día).
- A2 y C2: solo se predice desde la última sesión CERRADA. Se excluyen los
  tickers cuya fila de gold_inference es anterior (deslistados, descargas o
  gold.py fallidos) y se aborta si la sesión objetivo ya ha abierto (salvo
  `--allow-late`): predecir con la sesión en marcha no es una predicción.
- M4: el modelo más reciente se elige por la fecha del nombre, no por orden
  alfabético; se usan las columnas con que se entrenó (`feature_names`).
- M5: se guarda `date_origen` y nunca se sobrescribe una fila ya resuelta.

Uso:
    python predict.py --all-horizons               # día, semana y mes (lo que usa el pipeline diario)
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
import market_time  # noqa: E402
from config import DEFAULT_PREDICTION_HORIZON, MARKET_CALENDAR, MODELS_DIR, PREDICTION_HORIZONS  # noqa: E402
from model import ALL_FEATURE_NAMES, features_for_model  # noqa: E402

# Marcador de horizonte en el nombre del modelo (ver model.save_model,
# 2026-08-13, CONTEXTO.md "Horizontes de predicción: semana y mes"):
# "random_forest_h5_20260813172000" -> horizonte 5. Los modelos guardados
# ANTES de este cambio no llevan "_hN_" en el nombre — se tratan como
# horizonte 1 por convención (es lo único que existía entonces).
_HORIZON_MARKER_RE = re.compile(r"_h(\d+)_")
_TIMESTAMP_RE = re.compile(r"(\d{14})$")


def horizon_from_model_version(model_version: str) -> int:
    match = _HORIZON_MARKER_RE.search(model_version)
    return int(match.group(1)) if match else DEFAULT_PREDICTION_HORIZON


def timestamp_from_model_version(model_version: str) -> str:
    """"random_forest_h5_20260909170149" -> "20260909170149" ("" si no hay)."""
    match = _TIMESTAMP_RE.search(model_version)
    return match.group(1) if match else ""

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
    # Orden por la fecha del nombre (auditoría, M4): con orden alfabético,
    # "lightgbm_h1_2026..." nunca ganaba a "random_forest_h1_2026..." aunque
    # fuera más reciente.
    candidates = sorted(MODELS_DIR.glob("*.joblib"), key=lambda c: (timestamp_from_model_version(c.stem), c.stem))
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


def filter_current_rows(gold_inf: pd.DataFrame, origin: dt.date) -> pd.DataFrame:
    """Solo las filas de gold_inference fechadas en `origin` (la última
    sesión cerrada). Las anteriores son tickers con datos obsoletos (A2): se
    registran en el log y no se predicen."""
    dates = pd.to_datetime(gold_inf["date"]).dt.date
    stale = gold_inf[dates != origin]
    if not stale.empty:
        pares = [f"{t} ({d})" for t, d in zip(stale["ticker"], stale["date"])]
        detalle = ", ".join(pares[:15]) + (f" y {len(pares) - 15} más" if len(pares) > 15 else "")
        print(
            f"[predict] {len(stale)} ticker(s) excluidos por datos no actualizados a {origin}: {detalle}",
            file=sys.stderr,
        )
    return gold_inf[dates == origin].copy()


def predict_all(
    gold_inf: pd.DataFrame, model, model_version: str, horizon: int = DEFAULT_PREDICTION_HORIZON,
    feature_names: list[str] | None = None,
) -> pd.DataFrame:
    """Genera una predicción por fila de gold_inference. Devuelve un
    DataFrame listo para upsert en `predictions`.

    `date_predicha` es la sesión de mercado que cae `horizon` sesiones
    después de la fecha de `gold_inference` (1 = mañana, 5 = ~1 semana
    vista, 20 = ~1 mes vista). `date_origen` es esa fecha de gold_inference,
    que `track_predictions.py` usa para comparar el cierre real.

    `feature_names`: las columnas con que se entrenó el modelo (del bundle).
    Sin ella se asume el conjunto completo de 16 features, el de todos los
    modelos anteriores a la auditoría."""
    if gold_inf.empty:
        return pd.DataFrame()
    x = features_for_model(gold_inf, feature_names or ALL_FEATURE_NAMES)
    proba_up = model.predict_proba(x)[:, 1]
    pred_label = (proba_up >= 0.5).astype(int)

    now = dt.datetime.now()
    origins = pd.to_datetime(gold_inf["date"]).dt.date
    out = pd.DataFrame({
        "ticker": gold_inf["ticker"].to_numpy(),
        "date_predicha": [nth_trading_day(d, horizon) for d in origins],
        "date_origen": list(origins),
        "predicted_target_up_down": pred_label,
        "predicted_probability": proba_up,
        "model_version": model_version,
        "predicted_at": now,
    })
    return out


def upsert_predictions(df: pd.DataFrame, engine: Engine) -> int:
    """Inserta o actualiza predicciones, pero NUNCA toca una fila ya resuelta
    (auditoría, M5): antes, reejecutar predict.py para la misma fecha volvía
    a poner `actual_target_up_down` a NULL."""
    if df.empty:
        return 0
    records = df.to_dict(orient="records")
    stmt = sqlite_insert(dbmod.predictions)
    update_cols = {
        c: stmt.excluded[c]
        for c in ("predicted_target_up_down", "predicted_probability", "predicted_at", "date_origen")
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker", "date_predicha", "model_version"],
        set_=update_cols,
        where=dbmod.predictions.c.actual_target_up_down.is_(None),
    )
    with engine.begin() as conn:
        conn.execute(stmt, records)
    return len(df)


def run_for_horizon(
    engine: Engine, gold_inf: pd.DataFrame, horizon: int, model_version: str | None = None, allow_late: bool = False,
) -> int:
    """Predice un horizonte. Devuelve 0 si fue bien (o si no hay modelo para
    ese horizonte y se pidió con --all-horizons), 1 si hubo un problema."""
    if model_version:
        path = model_path_for_version(model_version)
        horizon = horizon_from_model_version(model_version)
    else:
        path = latest_model_path(horizon=horizon)
        model_version = path.stem

    origin = market_time.last_closed_session()
    current = filter_current_rows(gold_inf, origin)
    if current.empty:
        print(
            f"[predict] ninguna fila de gold_inference está fechada en la última sesión cerrada ({origin}). "
            "¿Ha corrido load.py + gold.py después del cierre?",
            file=sys.stderr,
        )
        return 1

    target_open = market_time.next_session_open(origin)
    if market_time.now_utc() >= target_open and not allow_late:
        print(
            f"[predict] la sesión siguiente a {origin} ya abrió ({target_open.tz_convert('Europe/Madrid'):%d/%m %H:%M} "
            "hora de Madrid): no se guardan predicciones hechas con la sesión en marcha. Ejecuta el pipeline "
            "después del cierre (22:20 en Madrid) o usa --allow-late.",
            file=sys.stderr,
        )
        return 1

    bundle = joblib.load(path)
    preds = predict_all(current, bundle["model"], model_version, horizon=horizon, feature_names=bundle.get("feature_names"))
    n = upsert_predictions(preds, engine)
    print(
        f"[predict] {n} predicción(es) guardadas (model_version={model_version}, horizonte={horizon}, origen={origin})",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model-version", help="model_version concreto (por defecto, el más reciente del horizonte elegido)")
    parser.add_argument(
        "--horizon", type=int, choices=sorted(PREDICTION_HORIZONS), default=DEFAULT_PREDICTION_HORIZON,
        help="sesiones de mercado vista: 1=día (por defecto), 5=semana, 20=mes — ignorado si se pasa "
        "--model-version (el horizonte se deduce del propio nombre del modelo)",
    )
    parser.add_argument("--all-horizons", action="store_true", help="predice todos los horizontes con modelo entrenado")
    parser.add_argument(
        "--allow-late", action="store_true",
        help="guardar predicciones aunque la sesión objetivo ya haya abierto (no recomendado)",
    )
    args = parser.parse_args(argv)

    engine = dbmod.get_engine()
    dbmod.init_db(engine)

    gold_inf = read_gold_inference(engine)
    if gold_inf.empty:
        print("[predict] gold_inference está vacío. Ejecuta antes gold.py.", file=sys.stderr)
        return 1

    if args.model_version:
        return run_for_horizon(engine, gold_inf, 0, model_version=args.model_version, allow_late=args.allow_late)

    horizons = sorted(PREDICTION_HORIZONS) if args.all_horizons else [args.horizon]
    failures = 0
    for horizon in horizons:
        try:
            failures += run_for_horizon(engine, gold_inf, horizon, allow_late=args.allow_late)
        except FileNotFoundError as exc:
            print(f"[predict] {exc}", file=sys.stderr)
            if not args.all_horizons:
                return 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
