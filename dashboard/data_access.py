"""
dashboard/data_access.py — capa de acceso a datos del dashboard.

SOLO LECTURA: el dashboard nunca escribe en la base de datos ni en
`models/` — todas las escrituras las hacen los módulos de `src/`
(download.py, load.py, gold.py, model.py, predict.py, news.py). Esta capa
existe para que las páginas de Streamlit (`pages/*.py`) no repitan consultas
SQL ni conozcan el esquema directamente — hablan con estas funciones, no con
`db.py` a pelo.

Usa los mismos `config.py`/`db.py` que el resto del pipeline (mismo
DB_URL) — no hay una base de datos ni una configuración distinta para el
dashboard.
"""

from __future__ import annotations

import sys
from pathlib import Path

import joblib
import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import Engine

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC_DIR))

import db as dbmod  # noqa: E402
import predict as predictmod  # noqa: E402
from model import FEATURE_NAMES, build_feature_matrix  # noqa: E402

# Traducciones de las features técnicas a lenguaje llano para la UI (ver
# CONTEXTO.md — el usuario pidió explícitamente que sea entendible para un
# usuario cualquiera, no solo "claro" para alguien con formación técnica).
# Cada feature lleva un título corto (para gráficos/tablas) y una
# explicación de una frase, sin jerga, como se le contaría a alguien que no
# sabe nada de estadística ni de bolsa.
FEATURE_LABELS: dict[str, str] = {
    "return_1d": "Cómo se movió ayer",
    "return_5d": "Cómo se ha movido en la última semana",
    "return_20d": "Cómo se ha movido en el último mes",
    "volatility_10d": "Lo agitada que ha estado últimamente",
    "close_to_ma5": "Precio de hoy vs. la última semana",
    "close_to_ma10": "Precio de hoy vs. las dos últimas semanas",
    "close_to_ma20": "Precio de hoy vs. el último mes",
    "rsi_14": "Si está \"sobrecomprada\" o \"sobrevendida\"",
    "macd_hist_norm": "Si la tendencia reciente está acelerando o frenando",
    "bb_pct_b": "Dónde está el precio dentro de su rango habitual",
    "bb_width": "Lo ancho que es ese rango habitual ahora mismo",
    "log_volume": "Cuánto se ha negociado",
    "relative_volume": "Si hoy se ha negociado más o menos de lo normal",
    "day_of_week": "Qué día de la semana es",
}

FEATURE_EXPLANATIONS: dict[str, str] = {
    "return_1d": "Si el precio subió o bajó ayer, y cuánto. El modelo tiene en cuenta el impulso más reciente.",
    "return_5d": "Si el precio subió o bajó en los últimos 5 días de mercado (una semana aprox.), y cuánto.",
    "return_20d": "Si el precio subió o bajó en los últimos 20 días de mercado (un mes aprox.), y cuánto.",
    "volatility_10d": (
        "Cuánto ha subido y bajado el precio en los últimos 10 días de mercado. Una acción \"agitada\" es "
        "más difícil de predecir que una tranquila."
    ),
    "close_to_ma5": "¿Está la acción más cara o más barata que su precio medio de los últimos 5 días de mercado?",
    "close_to_ma10": "¿Está la acción más cara o más barata que su precio medio de las últimas 2 semanas?",
    "close_to_ma20": "¿Está la acción más cara o más barata que su precio medio del último mes?",
    "rsi_14": (
        "Un indicador clásico (RSI) que va de 0 a 100: valores altos sugieren que se ha comprado mucho "
        "últimamente y podría \"enfriarse\"; valores bajos, que se ha vendido mucho y podría \"rebotar\"."
    ),
    "macd_hist_norm": (
        "Compara la tendencia de precio a corto plazo con la de más largo plazo. Ayuda a detectar cuando "
        "un movimiento empieza a perder fuerza, no solo si sube o baja."
    ),
    "bb_pct_b": (
        "Sitúa el precio de hoy dentro de su rango habitual reciente: cerca de 1 significa \"en la parte "
        "alta de lo normal\", cerca de 0, \"en la parte baja\"."
    ),
    "bb_width": "Si ese rango habitual reciente está más ancho (mucho vaivén) o más estrecho (precio tranquilo) de lo normal.",
    "log_volume": (
        "Cuántas acciones se han comprado y vendido. Mucho movimiento puede significar mucho interés "
        "(o mucho nerviosismo) por esa acción."
    ),
    "relative_volume": (
        "Compara el volumen de hoy con el volumen medio reciente de esa misma acción — más útil que el "
        "volumen en bruto para comparar una acción muy negociada con una que lo es menos."
    ),
    "day_of_week": "Lunes, martes... — por si hay patrones que se repiten según el día de la semana.",
}


def get_engine() -> Engine:
    return dbmod.get_engine()


def list_tickers(engine: Engine) -> list[str]:
    """Tickers con al menos una fila real en daily_prices (no solo los
    listados en config.TICKERS — puede haber diferencias puntuales, p. ej.
    justo tras corregir un ticker, ver CONTEXTO.md)."""
    with engine.begin() as conn:
        rows = conn.execute(
            select(dbmod.daily_prices.c.ticker).distinct().order_by(dbmod.daily_prices.c.ticker)
        ).fetchall()
    return [r[0] for r in rows]


def get_ticker_metadata(engine: Engine, ticker: str) -> dict:
    with engine.begin() as conn:
        row = conn.execute(select(dbmod.stocks).where(dbmod.stocks.c.ticker == ticker)).first()
    if row is None:
        return {"ticker": ticker, "nombre": None, "sector": None, "pais": None}
    return dict(row._mapping)


def get_price_history(engine: Engine, ticker: str, months: int = 12) -> pd.DataFrame:
    """Histórico de daily_prices de `ticker`, limitado a los últimos
    `months` meses para que el gráfico no se sature con 10 años de datos
    por defecto (el usuario puede ampliar la ventana en la UI)."""
    with engine.begin() as conn:
        rows = conn.execute(
            select(dbmod.daily_prices)
            .where(dbmod.daily_prices.c.ticker == ticker)
            .order_by(dbmod.daily_prices.c.date)
        ).fetchall()
    df = pd.DataFrame(rows, columns=["ticker", "date", "open", "high", "low", "close", "adj_close", "volume"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    cutoff = df["date"].max() - pd.DateOffset(months=months)
    return df[df["date"] >= cutoff].reset_index(drop=True)


def get_gold_train_for_ticker(engine: Engine, ticker: str, months: int = 12) -> pd.DataFrame:
    """Filas de gold_train de `ticker` (features + target real), para
    recalcular el backtest del modelo sobre ese ticker en la ventana
    reciente pedida por la UI. Se usa gold_train (no daily_prices
    directamente) porque ya trae las features calculadas — reutiliza el
    trabajo de gold.py en vez de reimplementar features.py aquí.

    Usa `result.keys()` (no una lista de columnas a mano) para los
    nombres de columna — igual que `model.read_gold_train` — para que
    añadir/quitar columnas en `db.gold_train` no obligue a mantener listas
    duplicadas en sincronía en varios archivos (ver CONTEXTO.md,
    "Ampliación de features", 2026-08-06)."""
    with engine.begin() as conn:
        result = conn.execute(
            dbmod.gold_train.select()
            .where(dbmod.gold_train.c.ticker == ticker)
            .order_by(dbmod.gold_train.c.date)
        )
        rows = result.fetchall()
        columns = result.keys()
    df = pd.DataFrame(rows, columns=columns)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    cutoff = df["date"].max() - pd.DateOffset(months=months)
    return df[df["date"] >= cutoff].reset_index(drop=True)


def get_latest_prediction(engine: Engine, ticker: str) -> dict | None:
    """Predicción más reciente (por predicted_at) para `ticker`, de
    cualquier model_version — normalmente solo hay uno, pero si se
    reentrena el modelo, nos quedamos con la más nueva."""
    with engine.begin() as conn:
        row = conn.execute(
            select(dbmod.predictions)
            .where(dbmod.predictions.c.ticker == ticker)
            .order_by(dbmod.predictions.c.predicted_at.desc())
            .limit(1)
        ).first()
    return dict(row._mapping) if row is not None else None


def load_latest_model() -> dict:
    """Carga el .joblib del modelo más reciente en MODELS_DIR. Devuelve el
    bundle completo (ver model.save_model) — puede no tener las claves de
    métricas si el modelo se entrenó antes de que model.py empezara a
    guardarlas (usar .get() con valor por defecto, nunca indexar directo)."""
    path = predictmod.latest_model_path()
    bundle = joblib.load(path)
    bundle["_model_version"] = path.stem
    return bundle


def predict_for_gold_rows(bundle: dict, gold_df: pd.DataFrame) -> pd.Series:
    """Aplica el modelo cargado a un DataFrame con las columnas de
    gold_train/gold_inference (features en crudo) y devuelve la predicción
    binaria (0/1) por fila, en el mismo orden. Usa build_feature_matrix de
    model.py para no duplicar la lógica de construcción de features."""
    x = build_feature_matrix(gold_df)
    return pd.Series(bundle["model"].predict(x), index=gold_df.index)


def feature_importances(bundle: dict) -> pd.DataFrame | None:
    """DataFrame (feature, etiqueta, importancia) ordenado descendente, o
    None si el modelo cargado no expone ninguna medida de importancia
    reconocida (no debería pasar con random_forest/logistic, pero se
    comprueba explícitamente en vez de asumir)."""
    model = bundle["model"]
    names = bundle.get("feature_names", FEATURE_NAMES)

    if hasattr(model, "feature_importances_"):
        values = model.feature_importances_
    elif hasattr(model, "coef_"):
        values = abs(model.coef_[0])
    else:
        return None

    df = pd.DataFrame({
        "feature": names,
        "etiqueta": [FEATURE_LABELS.get(n, n) for n in names],
        "explicacion": [FEATURE_EXPLANATIONS.get(n, "") for n in names],
        "importancia": values,
    })
    return df.sort_values("importancia", ascending=False).reset_index(drop=True)
