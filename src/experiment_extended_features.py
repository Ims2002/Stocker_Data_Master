"""
src/experiment_extended_features.py — experimento (2026-08-29/30, ver
CONTEXTO.md "Experimento: features ampliadas + ensemble"): ¿ampliar el
feature set con momentum relativo a mercado/sector y más indicadores
técnicos (Estocástico, ADX, OBV relativo), y/o combinar los 3 modelos ya
soportados en un ensemble, sube el accuracy por encima de los baselines?

Motivado por: el usuario preguntó por qué la predicción diaria de NVDA
bajaba de 50% (respuesta: comportamiento normal de una señal débil, no un
fallo) y a continuación pidió explorar mejoras reales del modelo antes de
aceptar ese techo — explícitamente "al margen de la versión actual", sin
tocar `models/` ni `predictions` (mismo criterio que
`experiment_single_ticker.py`, ya borrado tras responder su pregunta
original, ver CONTEXTO.md).

Ya estaba probado SIN éxito antes de este experimento (ver CONTEXTO.md,
"Plan B: Gradient Boosting + tuning" y "Preparación de noticias como
feature"): LightGBM con tuning de hiperparámetros no bate al baseline
mayoritario, ni tampoco el sentimiento de noticias como feature. Este
experimento prueba tres cosas más:

1. Momentum relativo a mercado/sector (`rel_strength_mkt_1d/5d`,
   `rel_strength_sector_1d/5d`): cuánto se mueve el ticker respecto a la
   media (equiponderada) de TODO el universo / de su sector ESE MISMO día
   — no hace falta descargar nada nuevo, se calcula transversalmente
   sobre `return_1d`/`return_5d`, que ya están en `gold_train`.
2. Indicadores técnicos nuevos, mismo criterio de "variable relativa/ya
   acotada" que el resto de `features.py`: Estocástico %K/%D (acotado
   [0,100], igual que RSI), ADX de Wilder (acotado ~[0,100]), y un OBV
   relativo (z-score sobre su propia media/desviación de 20 sesiones, en
   vez del OBV crudo, que es una suma acumulada sin escala y no
   comparable entre tickers).
3. Un ensemble por voto blando (media de `predict_proba`) de los 3 tipos
   de modelo ya soportados (`random_forest`, `lightgbm`, `logistic`),
   cada uno entrenado con el feature set AMPLIADO.

Limitación conocida de los indicadores técnicos nuevos: usan `high`/`low`
sin ajustar (no existen versiones ajustadas por splits/dividendos en el
esquema — ver `db.py`), mientras que el resto del pipeline usa
`adj_close`. Esto puede introducir una pequeña discontinuidad alrededor
de un split dentro de la ventana de cálculo — limitación aceptada y
documentada, no oculta (mismo criterio que las limitaciones ya anotadas
en `model.build_feature_matrix`, p. ej. `log_volume`).

Resultado real obtenido al verificar este script (horizonte día, split
temporal 2026-02-27 → 2026-08-27, ~481k filas train / ~26k test; RF con
n_estimators=100 en vez de 300 SOLO para la verificación en el sandbox de
esta sesión, por límite de cómputo — 2 CPUs, ~170s por comando; LightGBM
y Logistic sí con los hiperparámetros reales de producción):

    baseline mayoritario        acc=0.5065
    baseline persistencia       acc=0.4963
    random_forest (16, n=100)   acc=0.4981
    random_forest (24, n=100)   acc=0.5007  (+0.26pp con features nuevas)
    lightgbm (16, producción)   acc=0.4988
    lightgbm (24, producción)   acc=0.4993  (+0.05pp)
    logistic (16)                acc=0.5044  (el mejor individual)
    logistic (24)                acc=0.5034  (-0.10pp, empeora)
    ensemble (16)                 acc=0.5009
    ensemble (24)                 acc=0.5029  (+0.20pp, el mejor combinado)

Ningún modelo ni combinación —ni con las features nuevas ni el
ensemble— supera al baseline mayoritario (0.5065). La importancia de
features del Random Forest ampliado sí reparte peso razonable a las
nuevas variables (`rel_strength_mkt_1d`, `stoch_d`, `stoch_k` quedan en
la mitad de la tabla, no al fondo) — no son "ruido muerto", el modelo las
usa, pero no aportan la señal que faltaba. Mismo desenlace honesto que ya
documenta CONTEXTO.md: el techo de esta tarea con datos de mercado
públicos (precio/volumen/técnicos/sentimiento/momentum relativo) parece
estar en ~0.50-0.51, indistinguible del baseline ingenuo.

Para reproducir con los hiperparámetros REALES de producción en Random
Forest (n_estimators=300, este script ya lo pide por defecto — el
n=100 de arriba fue solo una limitación puntual de esta sesión, no de
este script), ejecuta en tu máquina:

    python src/experiment_extended_features.py
    python src/experiment_extended_features.py --horizon 5
    python src/experiment_extended_features.py --skip-ensemble  # más rápido, sin combinar los 3

Experimento de comparación, NO una alternativa de producción: no guarda
ningún `.joblib` en `models/`, no escribe en `predictions`. Solo imprime
el reporte comparativo.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod  # noqa: E402
from config import DEFAULT_PREDICTION_HORIZON, PREDICTION_HORIZONS  # noqa: E402
from gold import HORIZON_COLUMNS  # noqa: E402
from model import (  # noqa: E402
    _build_model,
    build_feature_matrix,
    evaluate,
    majority_baseline,
    persistence_baseline,
    read_gold_train,
    temporal_split,
)

EXTRA_FEATURE_COLUMNS = [
    "rel_strength_mkt_1d", "rel_strength_mkt_5d",
    "rel_strength_sector_1d", "rel_strength_sector_5d",
    "stoch_k", "stoch_d", "adx_14", "obv_zscore_20",
]


def _read_sector_map(engine) -> pd.DataFrame:
    with engine.begin() as conn:
        rows = conn.execute(dbmod.stocks.select()).fetchall()
        cols = conn.execute(dbmod.stocks.select()).keys()
    df = pd.DataFrame(rows, columns=cols)[["ticker", "sector"]]
    df["sector"] = df["sector"].fillna("Desconocido")
    return df


def add_relative_momentum(df: pd.DataFrame) -> pd.DataFrame:
    """Momentum del ticker relativo a la media equiponderada del universo
    completo / de su sector, EL MISMO día — no introduce lookahead porque
    solo usa `return_1d`/`return_5d` de ese mismo día (ya calculados sin
    mirar al futuro en `features.py`), agregados de forma transversal
    (entre tickers), no temporal."""
    out = df.copy()
    out["market_return_1d"] = out.groupby("date")["return_1d"].transform("mean")
    out["market_return_5d"] = out.groupby("date")["return_5d"].transform("mean")
    out["sector_return_1d"] = out.groupby(["date", "sector"])["return_1d"].transform("mean")
    out["sector_return_5d"] = out.groupby(["date", "sector"])["return_5d"].transform("mean")
    out["rel_strength_mkt_1d"] = out["return_1d"] - out["market_return_1d"]
    out["rel_strength_mkt_5d"] = out["return_5d"] - out["market_return_5d"]
    out["rel_strength_sector_1d"] = out["return_1d"] - out["sector_return_1d"]
    out["rel_strength_sector_5d"] = out["return_5d"] - out["sector_return_5d"]
    return out


def _stoch_adx_obv_for_ticker(
    g: pd.DataFrame, k_window: int = 14, d_window: int = 3, adx_window: int = 14
) -> pd.DataFrame:
    """Estocástico %K/%D, ADX de Wilder y OBV relativo, para UN ticker
    (no mezclar varios tickers — mismas ventanas rolling que el resto del
    proyecto, sin lookahead). Ver limitación de high/low sin ajustar en
    el docstring del módulo."""
    g = g.sort_values("date").reset_index(drop=True).copy()
    high, low, close = g["high"], g["low"], g["close"]

    lowest_low = low.rolling(k_window, min_periods=k_window).min()
    highest_high = high.rolling(k_window, min_periods=k_window).max()
    rng = highest_high - lowest_low
    with np.errstate(divide="ignore", invalid="ignore"):
        k = (close - lowest_low) / rng * 100
    # Rango plano (high==low durante toda la ventana, rarísimo pero
    # posible): mismo criterio que bb_pct_b en model.py, punto medio en
    # vez de NaN o división por cero.
    g["stoch_k"] = k.where(rng > 0, 50.0)
    g["stoch_d"] = g["stoch_k"].rolling(d_window, min_periods=d_window).mean()

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / adx_window, min_periods=adx_window, adjust=False).mean()
    plus_di = (
        100 * pd.Series(plus_dm, index=g.index)
        .ewm(alpha=1 / adx_window, min_periods=adx_window, adjust=False).mean() / atr
    )
    minus_di = (
        100 * pd.Series(minus_dm, index=g.index)
        .ewm(alpha=1 / adx_window, min_periods=adx_window, adjust=False).mean() / atr
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    g["adx_14"] = dx.ewm(alpha=1 / adx_window, min_periods=adx_window, adjust=False).mean()

    # OBV relativo: dirección tomada de return_1d (ya basado en
    # adj_close, coherente con el resto del pipeline) — solo la
    # MAGNITUD usa volumen crudo, que no depende de ajustes por split.
    direction = np.sign(g["return_1d"].fillna(0))
    obv = (direction * g["volume"]).cumsum()
    obv_mean20 = obv.rolling(20, min_periods=20).mean()
    obv_std20 = obv.rolling(20, min_periods=20).std()
    with np.errstate(divide="ignore", invalid="ignore"):
        z = (obv - obv_mean20) / obv_std20
    g["obv_zscore_20"] = z.where(obv_std20 > 0, 0.0)
    return g


def add_extra_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    return df.groupby("ticker", group_keys=False).apply(
        _stoch_adx_obv_for_ticker, include_groups=False
    ).join(df[["ticker"]])  # noqa: E501 — re-adjunta 'ticker' (excluido por include_groups=False)


def build_extended_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Las 16 features originales (`model.build_feature_matrix`) más las
    8 nuevas de este experimento. `stoch_k`/`stoch_d`/`adx_14` se dividen
    entre 100 por el mismo motivo que `rsi_14` en model.py: ya están
    acotadas, solo se reescalan a ~[0,1] para no dominar por magnitud."""
    x = build_feature_matrix(df)
    for col in ("rel_strength_mkt_1d", "rel_strength_mkt_5d", "rel_strength_sector_1d", "rel_strength_sector_5d"):
        x[col] = df[col]
    x["stoch_k"] = df["stoch_k"] / 100.0
    x["stoch_d"] = df["stoch_d"] / 100.0
    x["adx_14"] = df["adx_14"] / 100.0
    x["obv_zscore_20"] = df["obv_zscore_20"]
    return x


def prepare_extended_dataset(engine, horizon: int) -> pd.DataFrame:
    df = read_gold_train(engine)
    sector_map = _read_sector_map(engine)
    df = df.merge(sector_map, on="ticker", how="left")
    df["sector"] = df["sector"].fillna("Desconocido")

    target_col = HORIZON_COLUMNS[horizon][1]
    df = df[df[target_col].notna()].copy()

    df = add_relative_momentum(df)
    df = add_extra_technical_indicators(df)

    before = len(df)
    df = df.dropna(subset=EXTRA_FEATURE_COLUMNS).copy()
    print(
        f"[experimento] {before} -> {len(df)} filas tras descartar el calentamiento de los "
        "indicadores nuevos (ADX/Estocástico necesitan más historial que el resto).",
        file=sys.stderr,
    )
    return df


def _fit_eval(model_type: str, x_train, y_train, x_test, y_test) -> tuple[dict, np.ndarray]:
    model = _build_model(model_type)
    model.fit(x_train, y_train)
    proba = model.predict_proba(x_test)[:, 1]
    pred = (proba >= 0.5).astype(int)
    return evaluate(y_test, pred), proba


def run_experiment(horizon: int = DEFAULT_PREDICTION_HORIZON, skip_ensemble: bool = False) -> dict:
    engine = dbmod.get_engine()
    df = prepare_extended_dataset(engine, horizon)
    target_col = HORIZON_COLUMNS[horizon][1]

    train_df, test_df = temporal_split(df)
    y_train = train_df[target_col].astype(int)
    y_test = test_df[target_col].astype(int)
    print(
        f"[experimento] train: {len(train_df)} filas | test: {len(test_df)} filas "
        f"({test_df['date'].min()} a {test_df['date'].max()})",
        file=sys.stderr,
    )

    x_train_orig = build_feature_matrix(train_df)
    x_test_orig = build_feature_matrix(test_df)
    x_train_ext = build_extended_feature_matrix(train_df)
    x_test_ext = build_extended_feature_matrix(test_df)

    results = {
        "baseline_mayoritario": evaluate(y_test, majority_baseline(y_train, len(y_test))),
        "baseline_persistencia": evaluate(y_test, persistence_baseline(test_df)),
    }

    proba_orig, proba_ext = {}, {}
    for model_type in ("random_forest", "lightgbm", "logistic"):
        t0 = time.time()
        res_o, p_o = _fit_eval(model_type, x_train_orig, y_train, x_test_orig, y_test)
        res_e, p_e = _fit_eval(model_type, x_train_ext, y_train, x_test_ext, y_test)
        results[f"{model_type}_original16"] = res_o
        results[f"{model_type}_extendido24"] = res_e
        proba_orig[model_type] = p_o
        proba_ext[model_type] = p_e
        print(f"[experimento] {model_type}: listo en {time.time() - t0:.1f}s", file=sys.stderr)

    if not skip_ensemble:
        ens_orig = np.mean(list(proba_orig.values()), axis=0)
        ens_ext = np.mean(list(proba_ext.values()), axis=0)
        results["ensemble_original16"] = evaluate(y_test, (ens_orig >= 0.5).astype(int))
        results["ensemble_extendido24"] = evaluate(y_test, (ens_ext >= 0.5).astype(int))

    return results


def print_report(results: dict) -> None:
    order = [
        "baseline_mayoritario", "baseline_persistencia",
        "random_forest_original16", "random_forest_extendido24",
        "lightgbm_original16", "lightgbm_extendido24",
        "logistic_original16", "logistic_extendido24",
        "ensemble_original16", "ensemble_extendido24",
    ]
    print("[experimento] --- comparación (honestidad de resultado, ver CONTEXTO.md) ---", file=sys.stderr)
    print(f"[experimento] {'':26} {'acc':>7} {'prec':>7} {'rec':>7}", file=sys.stderr)
    for key in order:
        if key not in results:
            continue
        m = results[key]
        print(f"[experimento]   {key:26} {m['accuracy']:.4f}  {m['precision']:.4f}  {m['recall']:.4f}", file=sys.stderr)

    best_key = max(
        (k for k in results if not k.startswith("baseline_")),
        key=lambda k: results[k]["accuracy"],
    )
    best_acc = results[best_key]["accuracy"]
    baseline_acc = results["baseline_mayoritario"]["accuracy"]
    if best_acc > baseline_acc:
        print(
            f"[experimento] {best_key} SUPERA al baseline mayoritario ({best_acc:.4f} > {baseline_acc:.4f}).",
            file=sys.stderr,
        )
    else:
        print(
            f"[experimento] ninguna combinación probada supera al baseline mayoritario "
            f"(mejor: {best_key} con {best_acc:.4f} vs. {baseline_acc:.4f}) — resultado válido a "
            "reportar tal cual, no un fallo (ver CONTEXTO.md, Honestidad de resultado).",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--horizon", type=int, choices=sorted(PREDICTION_HORIZONS), default=DEFAULT_PREDICTION_HORIZON,
    )
    parser.add_argument(
        "--skip-ensemble", action="store_true",
        help="no entrenar el ensemble (más rápido si solo interesan los modelos individuales)",
    )
    args = parser.parse_args(argv)

    results = run_experiment(horizon=args.horizon, skip_ensemble=args.skip_ensemble)
    print_report(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
