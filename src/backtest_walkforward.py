"""
src/backtest_walkforward.py — evaluación walk-forward por años.

Auditoría del 16/09/2026 (M1): el modelo se evalúa con un único test de 6
meses, que es una sola muestra de régimen de mercado. Este script repite el
mismo contrato de evaluación (split temporal, purga de `horizon` sesiones
antes de cada corte, mismos baselines) año a año: para cada año de test Y,
entrena con todo lo anterior a Y y evalúa sobre Y. El resultado es una
distribución de accuracy por año frente a los baselines, no un único número.

No toca `models/` ni `predictions`: solo informa y guarda un JSON en
`data/reports/`.

Uso:
    python src/backtest_walkforward.py                          # horizonte día, RF, desde 2019
    python src/backtest_walkforward.py --horizon 5 --start-year 2020
    python src/backtest_walkforward.py --model logistic         # mucho más rápido
    python src/backtest_walkforward.py --class-weight none

Coste: un Random Forest por año sobre hasta ~500.000 filas. Con los valores
por defecto (100 árboles) cuenta con varios minutos por año en un portátil.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod  # noqa: E402
from config import DATA_DIR, DEFAULT_PREDICTION_HORIZON, PREDICTION_HORIZONS  # noqa: E402
from gold import HORIZON_COLUMNS  # noqa: E402
from model import (  # noqa: E402
    _build_model,
    evaluate,
    features_for_model,
    majority_baseline,
    persistence_baseline,
    read_gold_train,
)

REPORTS_DIR = DATA_DIR / "reports"


def walk_forward(
    df: pd.DataFrame, horizon: int, start_year: int, model_type: str, class_weight: str | None, n_estimators: int,
) -> list[dict]:
    target_col = HORIZON_COLUMNS[horizon][1]
    df = df[df[target_col].notna()].copy()
    df["date"] = pd.to_datetime(df["date"])
    last_year = int(df["date"].dt.year.max())
    results = []
    for year in range(start_year, last_year + 1):
        cutoff = pd.Timestamp(year=year, month=1, day=1)
        train_dates = np.sort(df.loc[df["date"] < cutoff, "date"].unique())
        if len(train_dates) <= horizon:
            continue
        train_dates = train_dates[:-horizon]  # purga, igual que model.temporal_split
        train = df[df["date"].isin(set(train_dates))]
        test = df[(df["date"] >= cutoff) & (df["date"] < pd.Timestamp(year=year + 1, month=1, day=1))]
        if train.empty or test.empty:
            continue

        model = _build_model(model_type, class_weight=class_weight)
        if model_type == "random_forest":
            model.set_params(n_estimators=n_estimators)
        x_train, x_test = features_for_model(train), features_for_model(test)
        y_train, y_test = train[target_col].astype(int), test[target_col].astype(int)
        model.fit(x_train, y_train)
        proba = model.predict_proba(x_test)[:, 1]
        pred = (proba >= 0.5).astype(int)

        row = {
            "year": year,
            "train_rows": len(train),
            "test_rows": len(test),
            "test_sessions": int(test["date"].nunique()),
            "pct_up": float(y_test.mean()),
            "model": evaluate(y_test, pred, proba),
            "majority": evaluate(y_test, majority_baseline(y_train, len(y_test))),
            "persistence": evaluate(y_test, persistence_baseline(test, horizon=horizon)),
        }
        results.append(row)
        print(
            f"[walkforward] {year}: modelo {row['model']['accuracy']:.3f} (AUC {row['model'].get('roc_auc', float('nan')):.3f}) | "
            f"mayoritaria {row['majority']['accuracy']:.3f} | persistencia {row['persistence']['accuracy']:.3f} | "
            f"subió {row['pct_up']:.1%} | {row['test_sessions']} sesiones",
            file=sys.stderr,
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--horizon", type=int, choices=sorted(PREDICTION_HORIZONS), default=DEFAULT_PREDICTION_HORIZON)
    parser.add_argument("--start-year", type=int, default=2019)
    parser.add_argument("--model", choices=["random_forest", "logistic", "lightgbm"], default="random_forest")
    parser.add_argument("--class-weight", choices=["balanced", "none"], default="balanced")
    parser.add_argument("--n-estimators", type=int, default=100)
    args = parser.parse_args(argv)

    df = read_gold_train(dbmod.get_engine())
    if df.empty:
        print("[walkforward] gold_train está vacío. Ejecuta antes gold.py.", file=sys.stderr)
        return 1
    class_weight = None if args.class_weight == "none" else args.class_weight
    results = walk_forward(df, args.horizon, args.start_year, args.model, class_weight, args.n_estimators)
    if not results:
        print("[walkforward] ningún año evaluable con esos parámetros.", file=sys.stderr)
        return 1

    wins = sum(
        r["model"]["accuracy"] > max(r["majority"]["accuracy"], r["persistence"]["accuracy"]) for r in results
    )
    print(f"[walkforward] el modelo supera a ambos baselines en {wins} de {len(results)} años.", file=sys.stderr)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"walkforward_h{args.horizon}_{args.model}_{dt.datetime.now():%Y%m%d%H%M%S}.json"
    out.write_text(json.dumps({"args": vars(args), "results": results}, indent=2), encoding="utf-8")
    print(f"[walkforward] informe: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
