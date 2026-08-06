"""
src/model.py — entrenamiento y evaluación del modelo de clasificación.

Decisión de implementación (documentada en CONTEXTO.md): en vez de un
modelo por ticker (cada uno con solo ~2.500 filas), se entrena UN modelo
agrupado (pooled) sobre `gold_train` de todos los tickers a la vez. Con
210 tickers eso son ~500.000 filas en vez de ~2.500 por serie aislada —
mucha más potencia estadística para una señal ya de por sí débil (ver
CONTEXTO.md, riesgos). Los tickers se tratan como observaciones
intercambiables: no se usa la identidad del ticker como feature (ver
docstring de `build_feature_matrix` para el razonamiento y la alternativa
con embeddings que queda pendiente).

Split temporal (CONTEXTO.md — nunca aleatorio): un único corte de fecha
GLOBAL (no por ticker) a `config.TEST_PERIOD_MONTHS` meses del final de
`gold_train`. Todos los tickers comparten el mismo punto de corte.

Baselines obligatorios, calculados sobre el mismo test:
- Clase mayoritaria del TRAIN (nunca del test, para no hacer trampa).
- Persistencia: repetir la tendencia del día anterior (signo de
  `return_1d`, que ya es "hoy subió/bajó respecto a ayer").

El modelo (Random Forest o Logistic Regression) solo se considera útil si
bate a ambos baselines de forma consistente en el test temporal — si no,
es un resultado válido que se reporta tal cual (ver CONTEXTO.md,
Honestidad de resultado), no se oculta ni maquilla.

Uso:
    python model.py                      # entrena random_forest (por defecto)
    python model.py --model logistic     # o logistic_regression
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, precision_score, recall_score
from sqlalchemy.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod  # noqa: E402
from config import MODELS_DIR, TEST_PERIOD_MONTHS  # noqa: E402

def read_gold_train(engine: Engine) -> pd.DataFrame:
    """Usa `result.keys()` (no una lista de columnas a mano) para los
    nombres de columna, para que añadir/quitar columnas en `db.gold_train`
    no obligue a mantener una lista duplicada aquí en sincronía (ver
    CONTEXTO.md, "Ampliación de features", 2026-08-06 — antes de este
    cambio, una lista `_GOLD_TRAIN_COLUMNS` a mano tenía que coincidir
    exactamente con el orden de columnas de tres archivos distintos)."""
    with engine.begin() as conn:
        result = conn.execute(dbmod.gold_train.select().order_by(dbmod.gold_train.c.date))
        rows = result.fetchall()
        columns = result.keys()
    return pd.DataFrame(rows, columns=columns)


def build_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Construye la matriz de features X a partir de las columnas
    almacenadas en gold_train/gold_inference.

    No se usan los niveles de precio en crudo (`open`/`close`/`adj_close`)
    ni `volume` en crudo como features directas: en un modelo agrupado
    multi-ticker, un precio de 500$ (ej. una acción cara) y uno de 20$
    (una barata) no son comparables en la misma escala, y el modelo podría
    aprender un atajo espurio ligado al nivel de precio de cada acción en
    vez de un patrón real. En su lugar se usan variables ya relativas/sin
    escala:

    - `return_1d`, `return_5d`, `return_20d`, `volatility_10d`: ya son
      porcentuales, comparables entre tickers tal cual.
    - `close_to_ma5/10/20`: precio actual respecto a su propia media móvil
      (ratio - 1), en vez del nivel absoluto de la media.
    - `rsi_14`: ya acotado en [0, 100] por definición — se divide entre
      100 solo para que quede en la misma escala aproximada [0, 1] que el
      resto de features relativas, no porque haga falta para comparar
      entre tickers.
    - `macd_hist_norm`: histograma MACD (línea - señal) dividido entre el
      precio de cierre. MACD "crudo" está en unidades de precio (como
      ma_5/10/20) y NO sería comparable entre una acción de 500$ y una de
      20$ sin este paso.
    - `bb_pct_b`, `bb_width`: reconstruidas aquí a partir de `ma_20`
      (centro de las bandas) y `price_std_20` (ya guardados) en vez de
      guardar las bandas ya calculadas — evita duplicar el mismo dato dos
      veces en el esquema. `bb_pct_b` es la posición del precio dentro de
      la banda (0=banda inferior, 1=banda superior, ya acotado y
      comparable); `bb_width` es el ancho de la banda relativo al propio
      precio (volatilidad reciente en términos relativos).
    - `log_volume`: log1p(volume) — mitiga la diferencia de escala entre
      tickers de mucho/poco volumen, aunque no la elimina del todo (ver
      limitación más abajo).
    - `relative_volume`: volumen de hoy respecto a su propia media móvil
      de 10 sesiones (ratio - 1) — a diferencia de `log_volume`, SÍ es
      comparable entre una acción muy líquida y una poco líquida, porque
      compara cada ticker contra sí mismo.
    - `day_of_week`: 0 (lunes) a 4 (viernes). Se deja como entero sin
      one-hot — Random Forest puede partir por umbrales sin asumir una
      relación de orden real entre días; si en el futuro se usa
      `logistic` como modelo principal (no solo como referencia), esto
      debería pasar a one-hot, porque una regresión lineal SÍ asumiría
      (incorrectamente) que "jueves" > "martes" en algún sentido
      numérico. No se ha hecho ahora porque Random Forest es el modelo
      por defecto y el que se usa en el dashboard.

    No se incluye el ticker como feature (ni one-hot ni embedding) en esta
    primera versión — con 210 tickers, un one-hot infla mucho la
    dimensionalidad para el volumen de datos disponible, y mezclaría
    "aprender la idiosincrasia de una acción concreta" con "aprender una
    señal técnica general", que es lo que este modelo agrupado busca
    aislar. Un embedding de ticker (aprendido o por clúster sectorial) es
    una mejora natural pendiente, no descartada — ver conversación de
    diseño del dashboard.

    Limitación conocida: `log_volume` sigue sin ser perfectamente
    comparable entre tickers de capitalización muy distinta; se mitiga
    (no se elimina del todo) añadiendo `relative_volume` como feature
    adicional en vez de sustituir `log_volume` — se deja que el modelo
    decida cuál de las dos pesa más (ver "Importancia de features" en el
    dashboard).

    Sentimiento de noticias (`news_sentiment_daily`, ver `news.py`):
    deliberadamente NO incluido todavía — a fecha de esta ampliación la
    tabla `news_articles` seguía con 0 filas reales (el backfill vive del
    cupo gratuito de 25 peticiones/día de Alpha Vantage y todavía no ha
    producido datos), así que añadirlo ahora solo metería una columna
    constante sin ninguna señal real. Pendiente explícito para cuando el
    backfill tenga cobertura de verdad — ver CONTEXTO.md.
    """
    x = pd.DataFrame(index=df.index)
    x["return_1d"] = df["return_1d"]
    x["return_5d"] = df["return_5d"]
    x["return_20d"] = df["return_20d"]
    x["volatility_10d"] = df["volatility_10d"]
    x["close_to_ma5"] = df["close"] / df["ma_5"] - 1
    x["close_to_ma10"] = df["close"] / df["ma_10"] - 1
    x["close_to_ma20"] = df["close"] / df["ma_20"] - 1
    x["rsi_14"] = df["rsi_14"] / 100.0
    x["macd_hist_norm"] = (df["macd_line"] - df["macd_signal"]) / df["close"]
    bb_upper = df["ma_20"] + 2 * df["price_std_20"]
    bb_lower = df["ma_20"] - 2 * df["price_std_20"]
    band_range = bb_upper - bb_lower
    # Si price_std_20 es exactamente 0 (precio perfectamente plano 20
    # sesiones seguidas, rarísimo con datos reales pero posible con datos
    # sintéticos/de test), el precio coincide con el centro de la banda
    # por definición matemática — se fija ese límite natural (0.5 = centro,
    # 0 = sin dispersión) en vez de propagar NaN o dividir por cero dentro
    # de sklearn. `errstate` silencia el warning de la división por cero
    # que numpy calcula igualmente aunque `.where()` descarte el resultado.
    with np.errstate(divide="ignore", invalid="ignore"):
        pct_b = (df["close"] - bb_lower) / band_range
    x["bb_pct_b"] = pct_b.where(band_range > 0, 0.5)
    x["bb_width"] = (band_range / df["ma_20"]).where(df["ma_20"] > 0, 0.0)
    x["log_volume"] = np.log1p(df["volume"])
    # Mismo criterio que bb_pct_b: si volume_ma_10 es 0 (10 sesiones
    # seguidas sin volumen — no debería pasar con acciones reales, pero se
    # cubre igual), "0 volumen hoy vs. 0 de media" no es informativo, se
    # fija en 0 (ni por encima ni por debajo de lo normal) en vez de NaN.
    with np.errstate(divide="ignore", invalid="ignore"):
        rel_volume = df["volume"] / df["volume_ma_10"] - 1
    x["relative_volume"] = rel_volume.where(df["volume_ma_10"] > 0, 0.0)
    x["day_of_week"] = df["day_of_week"]
    return x


FEATURE_NAMES = [
    "return_1d", "return_5d", "return_20d", "volatility_10d",
    "close_to_ma5", "close_to_ma10", "close_to_ma20",
    "rsi_14", "macd_hist_norm", "bb_pct_b", "bb_width",
    "log_volume", "relative_volume", "day_of_week",
]


def temporal_split(df: pd.DataFrame, test_period_months: int = TEST_PERIOD_MONTHS) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split cronológico único y global (no por ticker, no aleatorio): los
    últimos `test_period_months` meses de fecha (sobre TODO el panel) son
    test, el resto train."""
    max_date = pd.Timestamp(df["date"].max())
    cutoff = (max_date - pd.DateOffset(months=test_period_months)).date()
    train = df[df["date"] < cutoff].copy()
    test = df[df["date"] >= cutoff].copy()
    return train, test


def majority_baseline(train_y: pd.Series, test_len: int) -> np.ndarray:
    """Predice siempre la clase mayoritaria del TRAIN (nunca del test)."""
    majority = int(train_y.mode().iloc[0])
    return np.full(test_len, majority)


def persistence_baseline(test_df: pd.DataFrame) -> np.ndarray:
    """Predice que mañana repite la tendencia de hoy: signo de
    `return_1d` (retorno de hoy respecto a ayer, ya calculado en gold)."""
    return (test_df["return_1d"] > 0).astype(int).to_numpy()


def evaluate(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "precision": precision_score(y_true, y_pred, zero_division=0),
        "recall": recall_score(y_true, y_pred, zero_division=0),
    }


def _build_model(model_type: str):
    if model_type == "random_forest":
        return RandomForestClassifier(
            n_estimators=300, max_depth=8, min_samples_leaf=20,
            random_state=42, n_jobs=-1, class_weight="balanced",
        )
    if model_type == "logistic":
        return LogisticRegression(max_iter=1000, class_weight="balanced")
    raise ValueError(f"model_type desconocido: {model_type!r}")


def train_and_evaluate(df: pd.DataFrame, model_type: str = "random_forest") -> dict:
    """Entrena el modelo con split temporal y devuelve un dict con el
    modelo entrenado, las métricas del modelo y de ambos baselines sobre
    el mismo conjunto de test, y metadatos del split."""
    train_df, test_df = temporal_split(df)
    if train_df.empty or test_df.empty:
        raise ValueError(
            "Split temporal vacío (train o test): ¿hay suficiente histórico "
            "en gold_train para separar los últimos "
            f"{TEST_PERIOD_MONTHS} meses?"
        )

    x_train = build_feature_matrix(train_df)
    x_test = build_feature_matrix(test_df)
    y_train = train_df["target_up_down"].astype(int)
    y_test = test_df["target_up_down"].astype(int)

    model = _build_model(model_type)
    model.fit(x_train, y_train)
    y_pred_model = model.predict(x_test)

    y_pred_majority = majority_baseline(y_train, len(y_test))
    y_pred_persistence = persistence_baseline(test_df)

    return {
        "model": model,
        "model_type": model_type,
        "feature_names": FEATURE_NAMES,
        "train_rows": len(train_df),
        "test_rows": len(test_df),
        "train_date_max": str(train_df["date"].max()),
        "test_date_min": str(test_df["date"].min()),
        "test_date_max": str(test_df["date"].max()),
        "metrics_model": evaluate(y_test, y_pred_model),
        "metrics_majority_baseline": evaluate(y_test, y_pred_majority),
        "metrics_persistence_baseline": evaluate(y_test, y_pred_persistence),
    }


def save_model(result: dict) -> tuple[str, Path]:
    """Guarda el modelo entrenado en MODELS_DIR con un model_version único
    (tipo + timestamp). Devuelve (model_version, ruta).

    Además del modelo y los nombres de las features, guarda las métricas
    (modelo vs. ambos baselines) y los metadatos del split — el dashboard
    (ver `dashboard/`) las lee de aquí para mostrar el rendimiento del
    modelo sin tener que reentrenar (~500k filas) cada vez que alguien
    abre una página. Los modelos guardados ANTES de este cambio no tienen
    estas claves — el código que las lea debe usar `.get()` con
    valor por defecto, nunca asumir que existen."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model_version = f"{result['model_type']}_{datetime.now():%Y%m%d%H%M%S}"
    path = MODELS_DIR / f"{model_version}.joblib"
    joblib.dump(
        {
            "model": result["model"],
            "feature_names": result["feature_names"],
            "model_type": result["model_type"],
            "train_rows": result["train_rows"],
            "test_rows": result["test_rows"],
            "train_date_max": result["train_date_max"],
            "test_date_min": result["test_date_min"],
            "test_date_max": result["test_date_max"],
            "metrics_model": result["metrics_model"],
            "metrics_majority_baseline": result["metrics_majority_baseline"],
            "metrics_persistence_baseline": result["metrics_persistence_baseline"],
            "trained_at": datetime.now().isoformat(),
        },
        path,
    )
    return model_version, path


def print_report(result: dict) -> None:
    print("[model] --- split temporal ---", file=sys.stderr)
    print(
        f"[model] train: {result['train_rows']} filas (hasta {result['train_date_max']}) | "
        f"test: {result['test_rows']} filas ({result['test_date_min']} a {result['test_date_max']})",
        file=sys.stderr,
    )
    print("[model] --- métricas en test (honestidad de resultado, ver CONTEXTO.md) ---", file=sys.stderr)
    for label, key in (
        (f"modelo ({result['model_type']})", "metrics_model"),
        ("baseline clase mayoritaria", "metrics_majority_baseline"),
        ("baseline persistencia", "metrics_persistence_baseline"),
    ):
        m = result[key]
        print(
            f"[model]   {label:<28} acc={m['accuracy']:.3f}  prec={m['precision']:.3f}  rec={m['recall']:.3f}",
            file=sys.stderr,
        )

    beats_majority = result["metrics_model"]["accuracy"] > result["metrics_majority_baseline"]["accuracy"]
    beats_persistence = result["metrics_model"]["accuracy"] > result["metrics_persistence_baseline"]["accuracy"]
    if beats_majority and beats_persistence:
        print("[model] el modelo SUPERA a ambos baselines en este test.", file=sys.stderr)
    else:
        print(
            "[model] el modelo NO supera a ambos baselines de forma consistente en este test "
            "— resultado válido a reportar tal cual, no un fallo del pipeline (ver CONTEXTO.md).",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=["random_forest", "logistic"], default="random_forest")
    args = parser.parse_args(argv)

    engine = dbmod.get_engine()
    df = read_gold_train(engine)
    if df.empty:
        print("[model] gold_train está vacío. Ejecuta antes gold.py.", file=sys.stderr)
        return 1

    result = train_and_evaluate(df, model_type=args.model)
    print_report(result)
    model_version, path = save_model(result)
    print(f"[model] modelo guardado: {model_version} -> {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
