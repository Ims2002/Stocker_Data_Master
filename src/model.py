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

Horizontes (2026-08-13, ver CONTEXTO.md "Horizontes de predicción: semana
y mes"): además del horizonte a 1 sesión (día siguiente, por defecto),
gold_train ya trae targets a 5 y 20 sesiones (~semana/mes de mercado) —
`--horizon` elige cuál entrenar. Cada horizonte se guarda como un modelo
independiente (`model_version` con `_hN_`), así que puede haber un modelo
de cada horizonte conviviendo a la vez en MODELS_DIR.

Uso:
    python model.py                      # horizonte día, random_forest (por defecto)
    python model.py --horizon 5          # horizonte semana
    python model.py --horizon 20 --model logistic
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
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, precision_score, recall_score, roc_auc_score
from sqlalchemy.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod  # noqa: E402
from config import (  # noqa: E402
    DEFAULT_PREDICTION_HORIZON,
    MODEL_USE_NEWS_FEATURES,
    MODELS_DIR,
    PERMUTATION_IMPORTANCE_REPEATS,
    PERMUTATION_IMPORTANCE_SAMPLE,
    PREDICTION_HORIZONS,
    TEST_PERIOD_MONTHS,
)
from gold import HORIZON_COLUMNS  # noqa: E402

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
    df = pd.DataFrame(rows, columns=columns)
    # Defensa ante el caso real de la auditoría (C1): con el índice de
    # gold_train corrupto llegó a haber 8.042 filas duplicadas, con las que
    # se entrenaba sin saberlo.
    n_dup = int(df.duplicated(["ticker", "date"]).sum()) if not df.empty else 0
    if n_dup:
        print(
            f"[model] AVISO: {n_dup} fila(s) duplicadas en gold_train — se descartan. "
            "Ejecuta `python src/maintenance.py check`.",
            file=sys.stderr,
        )
        df = df.drop_duplicates(["ticker", "date"], keep="last")
    return df


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

    Sentimiento de noticias (2026-08-08, ver CONTEXTO.md "Preparación de
    noticias como feature"): `news_sentiment_3d` (media de
    `ticker_sentiment_score` suavizada a 3 sesiones, ya acotada en un
    rango tipo [-1, 1], comparable entre tickers tal cual, igual que
    `rsi_14`) y `news_volume_log` (log1p de `news_volume_3d`, el nº de
    artículos sumado en las últimas 3 sesiones — mismo criterio que
    `log_volume`: mitiga, no elimina del todo, la diferencia de escala
    entre un ticker muy mediático y uno que apenas sale en prensa).
    Ambas vienen ya en 0 (neutro/sin cobertura) para cualquier fecha o
    ticker sin noticias, no en NaN (ver `features.attach_news_features`).

    IMPORTANTE — no reentrenar todavía solo por esto: a fecha de esta
    ampliación el backfill de noticias solo cubre ~25/208 tickers (en
    marcha, ver CONTEXTO.md) — para la mayoría de filas de `gold_train`
    estas dos columnas valen 0 sin más, así que entrenar ahora mismo no
    mediría la señal real de esta feature, solo añadiría dimensionalidad
    sin información. Están aquí para que el pipeline esté listo en
    cuanto el backfill tenga cobertura suficiente, no como indicación de
    que ya toca reentrenar.
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
    x["news_sentiment_3d"] = df["news_sentiment_3d"]
    x["news_volume_log"] = np.log1p(df["news_volume_3d"])
    return x


NEWS_FEATURE_NAMES = ["news_sentiment_3d", "news_volume_log"]

# Todas las columnas que construye `build_feature_matrix`.
ALL_FEATURE_NAMES = [
    "return_1d", "return_5d", "return_20d", "volatility_10d",
    "close_to_ma5", "close_to_ma10", "close_to_ma20",
    "rsi_14", "macd_hist_norm", "bb_pct_b", "bb_width",
    "log_volume", "relative_volume", "day_of_week",
    *NEWS_FEATURE_NAMES,
]

# Las que usa un modelo NUEVO (auditoría, A3: sin noticias por defecto, ver
# config.MODEL_USE_NEWS_FEATURES). Cada bundle guarda su propia lista en
# `feature_names`, y `predict.py`/el dashboard seleccionan esas columnas —
# así los modelos antiguos (16 features) y los nuevos (14) conviven.
FEATURE_NAMES = ALL_FEATURE_NAMES if MODEL_USE_NEWS_FEATURES else [
    f for f in ALL_FEATURE_NAMES if f not in NEWS_FEATURE_NAMES
]


def features_for_model(df: pd.DataFrame, feature_names: list[str] | None = None) -> pd.DataFrame:
    """Matriz X con exactamente las columnas (y el orden) con que se entrenó
    el modelo. `feature_names=None` usa FEATURE_NAMES (modelo nuevo)."""
    x = build_feature_matrix(df)
    return x[list(feature_names or FEATURE_NAMES)]


def temporal_split(
    df: pd.DataFrame, test_period_months: int = TEST_PERIOD_MONTHS, purge_sessions: int = 0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split cronológico único y global (no por ticker, no aleatorio): los
    últimos `test_period_months` meses de fecha (sobre TODO el panel) son
    test, el resto train.

    `purge_sessions` (2026-09-09, ver CONTEXTO.md "Leakage en el split de
    horizontes 5/20: purga de sesiones antes del corte", feedback de un
    tutor): fuga de datos real detectada en horizontes >1. El target de
    una fila fechada `d` se construye en `gold.py` como
    `close.shift(-horizon)` — es decir, el cierre `horizon` SESIONES
    después de `d`. Con `purge_sessions=0` (comportamiento antiguo), una
    fila de TRAIN justo antes del corte puede tener una etiqueta
    calculada con un cierre que ya cae DENTRO del periodo de test: para
    horizonte 5, la fila fechada `cutoff - 5 sesiones` tiene como target
    el cierre de exactamente `cutoff` (ya es test). El modelo aprendería
    así, indirectamente, del movimiento de precio real del periodo que se
    supone que no ha visto — no es que vea filas de test, pero SÍ ve
    etiquetas construidas con precios de test. Con horizonte 20 el efecto
    es peor (hasta 20 sesiones de solape).

    Fix: purgar del train las `purge_sessions` sesiones de mercado
    inmediatamente anteriores al corte (`purge_sessions = horizon` en los
    llamadores) — así ninguna fila de train conserva una etiqueta que
    dependa de un cierre `>= cutoff`. Se aplica también a horizonte 1
    (purga de 1 sola sesión) por consistencia, aunque ahí el solape era
    de una sola sesión y el efecto práctico es mínimo (~208 filas de
    ~510k). El test NO se purga — sigue siendo `date >= cutoff` tal
    cual, sin cambios."""
    max_date = pd.Timestamp(df["date"].max())
    cutoff = (max_date - pd.DateOffset(months=test_period_months)).date()
    train_dates = np.sort(df.loc[df["date"] < cutoff, "date"].unique())
    if purge_sessions > 0:
        train_dates = train_dates[:-purge_sessions] if len(train_dates) > purge_sessions else train_dates[:0]
    train = df[df["date"].isin(set(train_dates))].copy()
    test = df[df["date"] >= cutoff].copy()
    return train, test


def majority_baseline(train_y: pd.Series, test_len: int) -> np.ndarray:
    """Predice siempre la clase mayoritaria del TRAIN (nunca del test)."""
    majority = int(train_y.mode().iloc[0])
    return np.full(test_len, majority)


# Retorno "del mismo plazo" que el horizonte, para el baseline de persistencia.
_PERSISTENCE_RETURN_COLUMN = {1: "return_1d", 5: "return_5d", 20: "return_20d"}


def persistence_baseline(test_df: pd.DataFrame, horizon: int = DEFAULT_PREDICTION_HORIZON) -> np.ndarray:
    """Predice que el próximo periodo repite la tendencia del último periodo
    del mismo plazo: signo de `return_1d` para el horizonte día, de
    `return_5d` para semana y de `return_20d` para mes.

    CAMBIO (auditoría 16/09/2026, M1): antes se usaba `return_1d` en los tres
    horizontes, un baseline más débil de lo justo a 5 y 20 sesiones, donde el
    momentum del mismo plazo es la referencia natural."""
    col = _PERSISTENCE_RETURN_COLUMN.get(horizon, "return_1d")
    return (test_df[col] > 0).astype(int).to_numpy()


def evaluate(y_true: np.ndarray, y_pred: np.ndarray, proba_up: np.ndarray | None = None) -> dict:
    """Accuracy, precision y recall; con `proba_up`, también ROC AUC, log
    loss y Brier (auditoría, M1): la accuracy con umbral 0,5 no dice si las
    probabilidades ordenan bien los casos. Los baselines no tienen
    probabilidad, así que para ellos solo se calculan las tres primeras."""
    out = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
    }
    if proba_up is not None and len(np.unique(y_true)) == 2:
        proba = np.clip(np.asarray(proba_up, dtype=float), 1e-6, 1 - 1e-6)
        out["roc_auc"] = float(roc_auc_score(y_true, proba))
        out["log_loss"] = float(log_loss(y_true, proba, labels=[0, 1]))
        out["brier"] = float(brier_score_loss(y_true, proba))
    return out


def permutation_importance_on_test(model, x_test: pd.DataFrame, y_test: pd.Series, random_state: int = 42) -> list[dict]:
    """Importancia por permutación sobre (una muestra de) el test (auditoría,
    M2): cuánto baja la accuracy al desordenar cada variable. A diferencia de
    la importancia MDI de Random Forest, no favorece a las variables
    continuas y mide lo que el modelo aprovecha fuera de entrenamiento."""
    from sklearn.inspection import permutation_importance

    if len(x_test) > PERMUTATION_IMPORTANCE_SAMPLE:
        idx = x_test.sample(PERMUTATION_IMPORTANCE_SAMPLE, random_state=random_state).index
        x_test, y_test = x_test.loc[idx], y_test.loc[idx]
    result = permutation_importance(
        model, x_test, y_test, scoring="accuracy",
        n_repeats=PERMUTATION_IMPORTANCE_REPEATS, random_state=random_state, n_jobs=-1,
    )
    return [
        {"feature": name, "mean": float(m), "std": float(sd)}
        for name, m, sd in zip(x_test.columns, result.importances_mean, result.importances_std)
    ]


def calibration_diagnostic(y_true, proba_up: np.ndarray, n_bins: int = 10) -> dict | None:
    """Diagnóstico de calibración de `predicted_probability` sobre un
    conjunto de test ya resuelto (2026-09-09, ver CONTEXTO.md
    "Calibración de predicted_probability", feedback de un tutor: "esa
    probabilidad no está necesariamente calibrada, así que no la
    vendería como confianza sin comprobar calibración").

    Se comprueba la CONFIANZA en la clase predicha
    (`max(proba_up, 1 - proba_up)`), no `proba_up` en crudo — es
    literalmente lo que se le enseña al usuario junto a la dirección
    predicha en el dashboard (ver `dashboard/views/dashboard.py` y
    `predicciones.py`, y `data_access.get_confidence_accuracy`, que ya
    usaba este mismo criterio de "confianza" desde antes). Si el modelo
    estuviera bien calibrado, un grupo de predicciones con confianza
    declarada del 60% debería acertar ~60% de las veces, ni más ni menos.

    Devuelve:
    - `brier_score`: Brier score clásico (`proba_up` vs. el resultado
      real 0/1 de "sube") — 0 es perfecto, 0.25 es lo que da predecir
      siempre 0.5 (el peor caso "no informativo" con clases balanceadas).
    - `reliability_table`: lista de cubos (por defecto `n_bins`,
      percentiles de confianza) con nº de casos, confianza media
      declarada y acierto real observado en ese cubo — la tabla/gráfico
      de fiabilidad que hace visible si "confianza" es una palabra
      honesta aquí o no.

    None si no hay datos suficientes para al menos 2 cubos distintos
    (no debería pasar con el tamaño real del test, pero evita reventar
    con conjuntos de prueba minúsculos)."""
    from sklearn.metrics import brier_score_loss

    y = np.asarray(y_true).astype(int)
    proba_up = np.asarray(proba_up, dtype=float)
    if len(y) < 2 * n_bins:
        return None

    brier = float(brier_score_loss(y, proba_up))

    confianza = np.maximum(proba_up, 1 - proba_up)
    pred_label = (proba_up >= 0.5).astype(int)
    acierto = (pred_label == y).astype(int)

    try:
        bin_idx = pd.qcut(confianza, q=n_bins, duplicates="drop", labels=False)
    except ValueError:
        return {"brier_score": brier, "reliability_table": []}

    tabla = pd.DataFrame({"bin_idx": bin_idx, "confianza": confianza, "acierto": acierto})
    resumen = (
        tabla.groupby("bin_idx")
        .agg(
            n=("acierto", "size"),
            confianza_media=("confianza", "mean"),
            confianza_min=("confianza", "min"),
            confianza_max=("confianza", "max"),
            acierto_real=("acierto", "mean"),
        )
        .reset_index(drop=True)
    )
    reliability_table = [
        {
            "n": int(row.n),
            "confianza_media": float(row.confianza_media),
            "confianza_min": float(row.confianza_min),
            "confianza_max": float(row.confianza_max),
            "acierto_real": float(row.acierto_real),
        }
        for row in resumen.itertuples()
    ]
    return {"brier_score": brier, "reliability_table": reliability_table}


def _build_model(model_type: str, class_weight: str | None = "balanced"):
    """`class_weight` (auditoría, M1): "balanced" empuja al modelo hacia la
    clase minoritaria y resta accuracy frente al baseline mayoritario, que es
    precisamente la métrica elegida. Se deja como opción (`--class-weight
    none`) para poder comparar ambos en igualdad de condiciones."""
    if model_type == "random_forest":
        return RandomForestClassifier(
            n_estimators=300, max_depth=8, min_samples_leaf=20,
            random_state=42, n_jobs=-1, class_weight=class_weight,
        )
    if model_type == "logistic":
        return LogisticRegression(max_iter=1000, class_weight=class_weight)
    if model_type == "lightgbm":
        # Import perezoso: lightgbm es una dependencia OPCIONAL (ver
        # requirements.txt) — el resto del pipeline (random_forest,
        # logistic) no debe dejar de funcionar si no está instalada.
        # Hiperparámetros por defecto razonables, sin tuning — para tuning
        # de verdad usar `tune_lightgbm()` / `--model lightgbm --tune`.
        from lightgbm import LGBMClassifier

        return LGBMClassifier(
            n_estimators=300, num_leaves=31, learning_rate=0.05,
            min_child_samples=50, class_weight=class_weight,
            random_state=42, n_jobs=-1, verbosity=-1,
        )
    raise ValueError(f"model_type desconocido: {model_type!r}")


def train_and_evaluate(
    df: pd.DataFrame, model_type: str = "random_forest", horizon: int = DEFAULT_PREDICTION_HORIZON,
    class_weight: str | None = "balanced", permutation: bool = True,
) -> dict:
    """Entrena el modelo con split temporal y devuelve un dict con el
    modelo entrenado, las métricas del modelo y de ambos baselines sobre
    el mismo conjunto de test, y metadatos del split.

    `horizon` (2026-08-13, ver CONTEXTO.md "Horizontes de predicción:
    semana y mes"): sesiones de mercado vista (1/5/20 = día/semana/mes,
    ver `config.PREDICTION_HORIZONS`). Selecciona qué columna de target de
    `gold_train` usar (`gold.HORIZON_COLUMNS`) y descarta antes del split
    las filas sin ese target calculado — las últimas N-1 sesiones de cada
    ticker, que sí están en `gold_train` (con el target a 1 sesión
    relleno) pero no tienen suficiente futuro para el horizonte largo (ver
    `gold.build_gold_frames`). Las features son las mismas para todos los
    horizontes; solo cambia qué se le pide adivinar al modelo.

    `purge_sessions=horizon` (2026-09-09, ver CONTEXTO.md "Leakage en el
    split de horizontes 5/20"): se pasa siempre el propio horizonte a
    `temporal_split` — es la cantidad exacta de sesiones cuya etiqueta,
    de no purgarse, se solaparía con el periodo de test (ver docstring de
    `temporal_split`)."""
    if horizon not in HORIZON_COLUMNS:
        raise ValueError(f"horizon desconocido: {horizon!r} (válidos: {sorted(HORIZON_COLUMNS)})")
    target_col = HORIZON_COLUMNS[horizon][1]
    df = df[df[target_col].notna()].copy()

    train_df, test_df = temporal_split(df, purge_sessions=horizon)
    if train_df.empty or test_df.empty:
        raise ValueError(
            "Split temporal vacío (train o test): ¿hay suficiente histórico "
            f"en gold_train (horizonte={horizon}) para separar los últimos "
            f"{TEST_PERIOD_MONTHS} meses?"
        )

    x_train = features_for_model(train_df)
    x_test = features_for_model(test_df)
    y_train = train_df[target_col].astype(int)
    y_test = test_df[target_col].astype(int)

    model = _build_model(model_type, class_weight=class_weight)
    model.fit(x_train, y_train)
    y_pred_model = model.predict(x_test)
    proba_up_test = model.predict_proba(x_test)[:, 1]

    y_pred_majority = majority_baseline(y_train, len(y_test))
    y_pred_persistence = persistence_baseline(test_df, horizon=horizon)

    return {
        "model": model,
        "model_type": model_type,
        "horizon": horizon,
        "class_weight": class_weight,
        "feature_names": list(FEATURE_NAMES),
        "permutation_importance": (
            permutation_importance_on_test(model, x_test, y_test) if permutation else None
        ),
        "train_rows": len(train_df),
        "test_rows": len(test_df),
        "train_date_max": str(train_df["date"].max()),
        "test_date_min": str(test_df["date"].min()),
        "test_date_max": str(test_df["date"].max()),
        "metrics_model": evaluate(y_test, y_pred_model, proba_up_test),
        "metrics_majority_baseline": evaluate(y_test, y_pred_majority),
        "metrics_persistence_baseline": evaluate(y_test, y_pred_persistence),
        "calibration": calibration_diagnostic(y_test, proba_up_test),
    }


def _time_series_folds(
    df: pd.DataFrame, n_splits: int = 3, purge_sessions: int = 0,
) -> list[tuple[pd.Series, pd.Series]]:
    """Cortes cronológicos (ventana creciente / walk-forward) para tuning
    de hiperparámetros, DENTRO del train oficial únicamente (nunca ve el
    test real). Un k-fold aleatorio normal (p. ej. `StratifiedKFold`,
    la técnica de los apuntes de referencia del usuario para tuning
    general) mezclaría fechas futuras dentro del "entrenamiento" de cada
    fold — rompería la misma regla de "nunca mezclar pasado y futuro" que
    ya rige `temporal_split` (ver CONTEXTO.md, "Contrato de evaluación").

    Fold i usa como entrenamiento todos los bloques cronológicos 0..i, y
    como validación el bloque i+1 (ventana creciente, no bloques fijos de
    tamaño igual desconectados entre sí) — así cada fold sigue pareciendo
    "predecir el futuro cercano a partir de todo el pasado disponible
    hasta ese punto", igual que el problema real.

    `purge_sessions` (2026-09-09, ver CONTEXTO.md "Leakage en el split de
    horizontes 5/20"): MISMO problema que en `temporal_split` pero en
    cada frontera train/validación de cada fold, no solo en el corte
    final train/test — las últimas filas del bloque de train de un fold
    pueden tener una etiqueta (horizonte >1) construida con precios que
    ya caen dentro del bloque de validación de ese mismo fold. Se purgan
    las últimas `purge_sessions` sesiones de cada bloque de train (los
    bloques ya vienen ordenados cronológicamente por construcción, así
    que basta con recortar el final del array concatenado).

    Devuelve máscaras booleanas (no arrays de posiciones) para poder
    indexar `df` directamente con `df[mask]` sin depender de que el
    índice esté reseteado."""
    dates = np.sort(df["date"].unique())
    blocks = np.array_split(dates, n_splits + 1)
    folds = []
    for i in range(n_splits):
        train_dates = np.concatenate(blocks[: i + 1])
        if purge_sessions > 0:
            train_dates = train_dates[:-purge_sessions] if len(train_dates) > purge_sessions else train_dates[:0]
        val_dates = set(blocks[i + 1])
        train_mask = df["date"].isin(set(train_dates))
        val_mask = df["date"].isin(val_dates)
        folds.append((train_mask, val_mask))
    return folds


# Espacio de búsqueda de hiperparámetros para LightGBM — rangos habituales
# de la industria, no ajustados a mano para este dataset en concreto (eso
# es precisamente lo que hace la búsqueda). `class_weight="balanced"` se
# deja fijo (no en el espacio de búsqueda) para poder comparar con
# random_forest en igualdad de condiciones, ya que ese también lo usa fijo.
_LGB_SEARCH_SPACE: dict[str, list] = {
    "num_leaves": [15, 31, 63, 127],
    "max_depth": [-1, 4, 6, 8],
    "learning_rate": [0.01, 0.03, 0.05, 0.1],
    "n_estimators": [100, 200, 400],
    "min_child_samples": [20, 50, 100, 200],
    "feature_fraction": [0.6, 0.8, 1.0],
    "bagging_fraction": [0.6, 0.8, 1.0],
}


def _sample_lgb_params(rng: np.random.Generator) -> dict:
    return {name: rng.choice(values).item() for name, values in _LGB_SEARCH_SPACE.items()}


def _make_lgbm(params: dict, random_state: int):
    from lightgbm import LGBMClassifier

    kwargs = dict(params)
    # bagging_fraction no hace nada en LightGBM sin bagging_freq > 0 — se
    # activa automáticamente cuando el valor muestreado implica submuestreo.
    kwargs["bagging_freq"] = 1 if kwargs.get("bagging_fraction", 1.0) < 1.0 else 0
    return LGBMClassifier(
        **kwargs, class_weight="balanced", random_state=random_state, n_jobs=-1, verbosity=-1,
    )


def tune_lightgbm(
    df: pd.DataFrame, n_iter: int = 20, n_splits: int = 3, random_state: int = 42,
    horizon: int = DEFAULT_PREDICTION_HORIZON,
) -> dict:
    """Búsqueda aleatoria de hiperparámetros para LightGBM con validación
    cronológica walk-forward (`_time_series_folds`, nunca k-fold
    aleatorio). Evalúa `n_iter` combinaciones sobre el TRAIN oficial (el
    mismo `temporal_split` que usa cualquier otro model_type), se queda
    con la de mejor accuracy media en los folds, la reentrena sobre TODO
    el train oficial, y la evalúa sobre el mismo test real que los demás
    modelos — comparación justa: el test real no se toca ni una sola vez
    durante la búsqueda de hiperparámetros, solo al final.

    `horizon`: mismo criterio que en `train_and_evaluate()` — selecciona
    la columna de target de `gold_train` y descarta antes de nada las
    filas sin ese target calculado (ver CONTEXTO.md, "Horizontes de
    predicción: semana y mes").

    Devuelve el mismo formato que `train_and_evaluate()` (compatible con
    `save_model`/`print_report`), más `best_params`, `cv_best_val_accuracy`
    y `tuning_trials` (todas las combinaciones probadas, para auditar la
    búsqueda a posteriori en vez de solo confiar en la ganadora)."""
    if horizon not in HORIZON_COLUMNS:
        raise ValueError(f"horizon desconocido: {horizon!r} (válidos: {sorted(HORIZON_COLUMNS)})")
    target_col = HORIZON_COLUMNS[horizon][1]
    df = df[df[target_col].notna()].copy()

    train_df, test_df = temporal_split(df, purge_sessions=horizon)
    if train_df.empty or test_df.empty:
        raise ValueError(
            "Split temporal vacío (train o test): ¿hay suficiente histórico "
            f"en gold_train (horizonte={horizon}) para separar los últimos {TEST_PERIOD_MONTHS} meses?"
        )

    # purge_sessions=horizon también en cada frontera train/validación de
    # los folds de tuning (2026-09-09, ver docstring de _time_series_folds
    # y CONTEXTO.md "Leakage en el split de horizontes 5/20") — mismo
    # problema, mismo fix, en cada fold interno de la búsqueda.
    folds = _time_series_folds(train_df, n_splits=n_splits, purge_sessions=horizon)
    rng = np.random.default_rng(random_state)

    best_params: dict | None = None
    best_score = -1.0
    trials: list[dict] = []
    for _ in range(n_iter):
        params = _sample_lgb_params(rng)
        fold_scores = []
        for train_mask, val_mask in folds:
            x_tr = features_for_model(train_df[train_mask])
            y_tr = train_df.loc[train_mask, target_col].astype(int)
            x_val = features_for_model(train_df[val_mask])
            y_val = train_df.loc[val_mask, target_col].astype(int)
            fold_model = _make_lgbm(params, random_state)
            fold_model.fit(x_tr, y_tr)
            fold_scores.append(accuracy_score(y_val, fold_model.predict(x_val)))
        mean_score = float(np.mean(fold_scores))
        trials.append({"params": params, "mean_val_accuracy": mean_score, "fold_scores": fold_scores})
        if mean_score > best_score:
            best_score = mean_score
            best_params = params

    x_train = features_for_model(train_df)
    x_test = features_for_model(test_df)
    y_train = train_df[target_col].astype(int)
    y_test = test_df[target_col].astype(int)

    final_model = _make_lgbm(best_params, random_state)
    final_model.fit(x_train, y_train)
    y_pred_model = final_model.predict(x_test)
    proba_up_test = final_model.predict_proba(x_test)[:, 1]

    y_pred_majority = majority_baseline(y_train, len(y_test))
    y_pred_persistence = persistence_baseline(test_df, horizon=horizon)

    return {
        "model": final_model,
        "model_type": "lightgbm",
        "horizon": horizon,
        "class_weight": "balanced",
        "feature_names": list(FEATURE_NAMES),
        "permutation_importance": permutation_importance_on_test(final_model, x_test, y_test),
        "train_rows": len(train_df),
        "test_rows": len(test_df),
        "train_date_max": str(train_df["date"].max()),
        "test_date_min": str(test_df["date"].min()),
        "test_date_max": str(test_df["date"].max()),
        "metrics_model": evaluate(y_test, y_pred_model, proba_up_test),
        "metrics_majority_baseline": evaluate(y_test, y_pred_majority),
        "metrics_persistence_baseline": evaluate(y_test, y_pred_persistence),
        "calibration": calibration_diagnostic(y_test, proba_up_test),
        "best_params": best_params,
        "cv_best_val_accuracy": best_score,
        "tuning_trials": trials,
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
    valor por defecto, nunca asumir que existen.

    Si `result` viene de `tune_lightgbm()` (tiene `best_params`), también
    se guardan `best_params`/`cv_best_val_accuracy` para dejar constancia
    de con qué hiperparámetros se entrenó — NO se guarda `tuning_trials`
    (puede ser largo y es solo de interés puntual al momento de tunear,
    no algo que el dashboard necesite cargar en cada arranque).

    `model_version` incluye el horizonte (`_hN_`, 2026-08-13, ver
    CONTEXTO.md "Horizontes de predicción: semana y mes") para poder tener
    varios modelos (día/semana/mes) conviviendo en `MODELS_DIR` a la vez y
    elegir el correcto por horizonte en `predict.py`/el dashboard, sin
    tocar el esquema de `predictions` (su `model_version` es un texto
    libre, así que no hace falta migrar nada). Los modelos guardados ANTES
    de este cambio no tienen `_hN_` en su nombre — se tratan como
    horizonte 1 por convención (ver `predict.latest_model_path`)."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    horizon = result.get("horizon", DEFAULT_PREDICTION_HORIZON)
    model_version = f"{result['model_type']}_h{horizon}_{datetime.now():%Y%m%d%H%M%S}"
    path = MODELS_DIR / f"{model_version}.joblib"
    bundle = {
        "model": result["model"],
        "feature_names": result["feature_names"],
        "model_type": result["model_type"],
        "horizon": horizon,
        "train_rows": result["train_rows"],
        "test_rows": result["test_rows"],
        "train_date_max": result["train_date_max"],
        "test_date_min": result["test_date_min"],
        "test_date_max": result["test_date_max"],
        "metrics_model": result["metrics_model"],
        "metrics_majority_baseline": result["metrics_majority_baseline"],
        "metrics_persistence_baseline": result["metrics_persistence_baseline"],
        "calibration": result.get("calibration"),
        "class_weight": result.get("class_weight"),
        "permutation_importance": result.get("permutation_importance"),
        "target_price_column": "adj_close",
        "trained_at": datetime.now().isoformat(),
    }
    if "best_params" in result:
        bundle["best_params"] = result["best_params"]
        bundle["cv_best_val_accuracy"] = result.get("cv_best_val_accuracy")
    joblib.dump(bundle, path)
    return model_version, path


def print_report(result: dict) -> None:
    horizon = result.get("horizon", DEFAULT_PREDICTION_HORIZON)
    print(
        f"[model] --- horizonte: {horizon} sesión(es) ({PREDICTION_HORIZONS.get(horizon, '?')}) ---",
        file=sys.stderr,
    )
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
        extra = (
            f"  auc={m['roc_auc']:.3f}  logloss={m['log_loss']:.4f}  brier={m['brier']:.4f}"
            if "roc_auc" in m else ""
        )
        print(
            f"[model]   {label:<28} acc={m['accuracy']:.3f}  prec={m['precision']:.3f}  rec={m['recall']:.3f}{extra}",
            file=sys.stderr,
        )
    calibration = result.get("calibration")
    if calibration:
        print(
            "[model] --- calibración de predicted_probability (ver CONTEXTO.md, "
            "'Calibración de predicted_probability') ---",
            file=sys.stderr,
        )
        print(f"[model]   brier_score={calibration['brier_score']:.4f} (0=perfecto, 0.25=no informativo)", file=sys.stderr)
        for b in calibration["reliability_table"]:
            print(
                f"[model]   confianza {b['confianza_min']:.2f}-{b['confianza_max']:.2f} "
                f"(media {b['confianza_media']:.2f}, n={b['n']:>4}) -> acierto real {b['acierto_real']:.2f}",
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

    if "best_params" in result:
        print(
            f"[model] --- tuning: mejor accuracy media en validación walk-forward = "
            f"{result['cv_best_val_accuracy']:.3f} ---",
            file=sys.stderr,
        )
        print(f"[model]   hiperparámetros: {result['best_params']}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=["random_forest", "logistic", "lightgbm"], default="random_forest")
    parser.add_argument(
        "--tune", action="store_true",
        help="solo válido con --model lightgbm: busca hiperparámetros con validación walk-forward "
        "(tune_lightgbm) en vez de usar los valores por defecto.",
    )
    parser.add_argument("--tune-iterations", type=int, default=20, help="combinaciones a probar con --tune")
    parser.add_argument(
        "--class-weight", choices=["balanced", "none"], default="balanced",
        help="ponderación de clases (auditoría M1: 'none' suele dar más accuracy frente al baseline mayoritario)",
    )
    parser.add_argument(
        "--no-permutation", action="store_true",
        help="no calcular la importancia por permutación sobre el test (ahorra unos minutos)",
    )
    parser.add_argument(
        "--horizon", type=int, choices=sorted(PREDICTION_HORIZONS), default=DEFAULT_PREDICTION_HORIZON,
        help="sesiones de mercado vista: 1=día (por defecto), 5=semana, 20=mes "
        "(ver CONTEXTO.md, 'Horizontes de predicción: semana y mes')",
    )
    args = parser.parse_args(argv)

    if args.tune and args.model != "lightgbm":
        print("[model] --tune solo está soportado con --model lightgbm por ahora.", file=sys.stderr)
        return 1

    engine = dbmod.get_engine()
    df = read_gold_train(engine)
    if df.empty:
        print("[model] gold_train está vacío. Ejecuta antes gold.py.", file=sys.stderr)
        return 1

    if args.tune:
        result = tune_lightgbm(df, n_iter=args.tune_iterations, horizon=args.horizon)
    else:
        result = train_and_evaluate(
            df, model_type=args.model, horizon=args.horizon,
            class_weight=None if args.class_weight == "none" else args.class_weight,
            permutation=not args.no_permutation,
        )
    print_report(result)
    model_version, path = save_model(result)
    print(f"[model] modelo guardado: {model_version} -> {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
