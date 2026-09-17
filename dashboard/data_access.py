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

import datetime as dt
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.engine import Engine

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC_DIR))

import db as dbmod  # noqa: E402
import predict as predictmod  # noqa: E402
from config import DATA_DIR, DEFAULT_PREDICTION_HORIZON, PREDICTION_HORIZONS, STALE_TICKER_DAYS  # noqa: E402
from model import ALL_FEATURE_NAMES, calibration_diagnostic, features_for_model  # noqa: E402

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
    "news_sentiment_3d": "Tono de las noticias recientes",
    "news_volume_log": "Cuánto se ha hablado de la acción en prensa",
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
    "news_sentiment_3d": (
        "Tono medio de las noticias publicadas sobre la acción en los últimos 3 días de mercado: positivo, "
        "negativo o neutro. Vale 0 si no hay noticias recientes o cobertura todavía."
    ),
    "news_volume_log": (
        "Cuánto se ha escrito sobre la acción en prensa en los últimos 3 días — no el tono, solo la "
        "cantidad. Vale 0 si no hay cobertura."
    ),
}


def get_engine() -> Engine:
    return dbmod.get_engine()


def _current_tickers_subquery():
    """Tickers con datos recientes: su última fecha en daily_prices está a
    menos de STALE_TICKER_DAYS días naturales de la más reciente del
    universo (auditoría, A2). Excluye deslistados como EA, cuyo último dato
    es de agosto, y tickers cuya descarga lleva días fallando."""
    from sqlalchemy import func

    dp = dbmod.daily_prices
    last_by_ticker = select(dp.c.ticker, func.max(dp.c.date).label("last_date")).group_by(dp.c.ticker).subquery()
    global_max = select(func.max(dp.c.date)).scalar_subquery()
    return select(last_by_ticker.c.ticker).where(
        func.julianday(global_max) - func.julianday(last_by_ticker.c.last_date) <= STALE_TICKER_DAYS
    )


def list_tickers(engine: Engine) -> list[str]:
    """Tickers con datos recientes en daily_prices (ver
    `_current_tickers_subquery`), en orden alfabético."""
    with engine.begin() as conn:
        rows = conn.execute(_current_tickers_subquery().order_by(text("ticker"))).fetchall()
    return sorted(r[0] for r in rows)


def get_pipeline_status() -> dict | None:
    """Resultado de la última ejecución de `src/run_pipeline.py`
    (`data/pipeline_status.json`), o None si no existe (p. ej. la demo)."""
    import json

    path = DATA_DIR / "pipeline_status.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def ticker_logo_url(ticker: str, size: int = 48) -> str | None:
    """URL del logo de la empresa vía la Logo API de Logo.dev (2026-08-28,
    ver CONTEXTO.md "Logos de empresa en el dashboard") — parámetros
    verificados contra la documentación oficial
    (logo.dev/docs/logo-images/get), no adivinados:
    - `format=png` + `theme=light`: el formato por defecto (`jpg`) no
      tiene transparencia, así que el logo queda dentro de una caja
      blanca — con PNG transparente y `theme=light`, los logos con
      colores claros se invierten para seguir siendo visibles sobre el
      fondo blanco del dashboard (ver "Tip" de la documentación oficial).
    - `retina=true`: devuelve el doble de resolución real que `size`
      (píxeles lógicos), para que no se vea borroso en pantallas de alta
      densidad.
    - `fallback` se deja en su valor por defecto (`monogram`): si
      Logo.dev no tiene el logo de un ticker, devuelve un monograma en
      vez de un error, así `st.image()` nunca muestra un icono de imagen
      rota (Streamlit no tiene un `onerror` de HTML al que engancharse).

    None si `LOGO_DEV_TOKEN` no está configurada — en ese caso el
    dashboard simplemente no muestra logos, no rompe nada. El token es
    una "publishable key" pensada para ir en una URL de `<img>` (no hace
    falta protegerla como un secreto de servidor), pero se lee de
    config/entorno igual que cualquier otra clave, nunca hardcodeada."""
    from config import LOGO_DEV_TOKEN

    if not LOGO_DEV_TOKEN:
        return None
    return (
        f"https://img.logo.dev/ticker/{ticker}"
        f"?token={LOGO_DEV_TOKEN}&size={size}&format=png&theme=light&retina=true"
    )


def default_ticker_index(tickers: list[str]) -> int:
    """Índice a usar como valor por defecto de un selectbox de ticker.

    Antes los tres selectores de ticker del dashboard usaban `index=0` a
    secas sobre una lista ordenada ALFABÉTICAMENTE (`list_tickers()` /
    `get_tickers_by_sector()`, ambas `ORDER BY ticker`) — eso hacía que
    AAPL saliera seleccionado por defecto sin que nadie lo hubiera
    decidido así, solo por ser el primero en orden alfabético (2026-08-28,
    petición del usuario: NVDA como acción principal). Devuelve el índice
    de `config.DEFAULT_TICKER` (NVDA, el primero de `config.TICKERS`) si
    está en la lista dada; si no (p. ej. un filtro de sector que excluye a
    NVDA), cae al primero de la lista, igual que antes."""
    from config import DEFAULT_TICKER

    if DEFAULT_TICKER in tickers:
        return tickers.index(DEFAULT_TICKER)
    return 0


def get_latest_data_date(engine: Engine) -> dt.date | None:
    """Fecha más reciente cargada en `daily_prices`, sobre TODOS los
    tickers — para el aviso de "datos actualizados hasta" del Dashboard
    (2026-08-27, ver CONTEXTO.md "Roadmap v1 (MVP para publicar)"): en
    la demo publicada con datos congelados, esta fecha deja de avanzar
    el día que se generó la foto (`data/stocker_demo.db`, ver
    `src/export_demo_db.py`) — es la forma más honesta de que quien
    visite la demo sepa que no es un dato en vivo, sin necesitar
    detectar "modo demo" explícitamente en ningún sitio. None si
    `daily_prices` está vacía (pipeline no ejecutado todavía)."""
    from sqlalchemy import func

    with engine.begin() as conn:
        return conn.execute(select(func.max(dbmod.daily_prices.c.date))).scalar()


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


def get_latest_prediction(engine: Engine, ticker: str, horizon: int = DEFAULT_PREDICTION_HORIZON) -> dict | None:
    """Predicción más reciente (por predicted_at) para `ticker` en un
    horizonte concreto (2026-08-13, ver CONTEXTO.md "Horizontes de
    predicción: semana y mes") — antes de esa fecha solo existía
    horizonte 1 y esta función no filtraba por él. El horizonte no es una
    columna de `predictions` (para no migrar el esquema): se deduce del
    propio `model_version` con `predict.horizon_from_model_version`, igual
    que en `track_predictions.py`. Se filtra en Python (no en SQL) porque
    es sobre pocas filas por ticker, nunca sobre toda la tabla."""
    with engine.begin() as conn:
        rows = conn.execute(
            select(dbmod.predictions)
            .where(dbmod.predictions.c.ticker == ticker)
            .order_by(dbmod.predictions.c.predicted_at.desc())
        ).fetchall()
    for row in rows:
        record = dict(row._mapping)
        if predictmod.horizon_from_model_version(record["model_version"]) == horizon:
            return record
    return None


def latest_model_version(horizon: int = DEFAULT_PREDICTION_HORIZON) -> str | None:
    """Nombre del modelo más reciente de un horizonte, o None. Las páginas lo
    usan como clave de caché (auditoría, M4): con `@st.cache_resource` sin
    clave, el dashboard seguía usando el modelo antiguo tras reentrenar hasta
    reiniciar la app."""
    try:
        return predictmod.latest_model_path(horizon=horizon).stem
    except FileNotFoundError:
        return None


def load_latest_model(horizon: int = DEFAULT_PREDICTION_HORIZON) -> dict:
    """Carga el .joblib del modelo más reciente de un horizonte concreto
    en MODELS_DIR (2026-08-13, ver CONTEXTO.md "Horizontes de predicción:
    semana y mes" — antes de esa fecha solo existía horizonte 1).
    Devuelve el bundle completo (ver model.save_model) — puede no tener
    las claves de métricas si el modelo se entrenó antes de que model.py
    empezara a guardarlas (usar .get() con valor por defecto, nunca
    indexar directo). Lanza FileNotFoundError si ese horizonte concreto
    todavía no tiene ningún modelo entrenado (normal para 5/20 hasta que
    alguien ejecute `model.py --horizon 5/20`)."""
    path = predictmod.latest_model_path(horizon=horizon)
    bundle = joblib.load(path)
    bundle["_model_version"] = path.stem
    return bundle


def predict_for_gold_rows(bundle: dict, gold_df: pd.DataFrame) -> pd.Series:
    """Aplica el modelo cargado a un DataFrame con las columnas de
    gold_train/gold_inference (features en crudo) y devuelve la predicción
    binaria (0/1) por fila, en el mismo orden. Usa build_feature_matrix de
    model.py para no duplicar la lógica de construcción de features."""
    x = features_for_model(gold_df, bundle.get("feature_names") or ALL_FEATURE_NAMES)
    return pd.Series(bundle["model"].predict(x), index=gold_df.index)


def get_resolved_predictions(engine: Engine) -> pd.DataFrame:
    """Predicciones reales ya resueltas (`actual_target_up_down IS NOT
    NULL`, ver `src/track_predictions.py`) — el seguimiento real en
    producción, no backtest. Nunca se agrega sin `model_version`: mezclar
    versiones distintas en el mismo cálculo de acierto es engañoso (ver
    CONTEXTO.md, "Seguimiento real de predicciones: primer análisis",
    2026-08-13 — un modelo obsoleto sobre un día de mercado atípico
    hundió el agregado sin que hubiera ningún problema real detrás)."""
    with engine.begin() as conn:
        result = conn.execute(
            select(dbmod.predictions)
            .where(dbmod.predictions.c.actual_target_up_down.is_not(None))
            .order_by(dbmod.predictions.c.date_predicha)
        )
        rows = result.fetchall()
        columns = result.keys()
    df = pd.DataFrame(rows, columns=columns)
    if df.empty:
        return df
    df["date_predicha"] = pd.to_datetime(df["date_predicha"])
    df["acierto"] = df["predicted_target_up_down"] == df["actual_target_up_down"]
    # Baseline "siempre sube" en las mismas predicciones (auditoría, M1): sin
    # referencia, un 48 % de acierto no dice si el modelo aporta algo.
    df["acierto_siempre_sube"] = df["actual_target_up_down"] == 1
    return df


# --- Noticias y sentimiento (2026-08-13, ver CONTEXTO.md "Cuadros de mando
# de noticias y sentimiento") -------------------------------------------

SENTIMENT_LABEL_ORDER = ["Bearish", "Somewhat-Bearish", "Neutral", "Somewhat-Bullish", "Bullish"]
SENTIMENT_LABEL_ES: dict[str, str] = {
    "Bearish": "Muy negativo",
    "Somewhat-Bearish": "Algo negativo",
    "Neutral": "Neutro",
    "Somewhat-Bullish": "Algo positivo",
    "Bullish": "Muy positivo",
}


def news_coverage_status(engine: Engine) -> dict:
    """Cuántos tickers tienen ya al menos un artículo, sobre el total de
    `config.TICKERS` — el backfill de noticias (`src/news.py`) sigue en
    marcha, así que esto cambia día a día (ver CONTEXTO.md)."""
    with engine.begin() as conn:
        total_tickers = len(conn.execute(select(dbmod.stocks.c.ticker)).fetchall())
        covered = conn.execute(text("SELECT COUNT(DISTINCT ticker) FROM news_articles")).scalar()
        total_articles = conn.execute(text("SELECT COUNT(*) FROM news_articles")).scalar()
    return {"tickers_cubiertos": covered or 0, "tickers_totales": total_tickers, "articulos_totales": total_articles or 0}


def get_daily_sentiment_for_ticker(engine: Engine, ticker: str, months: int = 12) -> pd.DataFrame:
    """Serie diaria (`date`, `avg_sentiment_score`, `n_articles`) de la
    vista `news_sentiment_daily` para un ticker — solo trae fila para los
    días con al menos un artículo (ver `db.py`). Vía `text()` porque es
    una vista sin `Table()` de SQLAlchemy, igual que
    `gold.read_news_sentiment()`."""
    query = text(
        "SELECT date, avg_sentiment_score, avg_relevance_score, n_articles "
        "FROM news_sentiment_daily WHERE ticker = :ticker ORDER BY date"
    )
    with engine.begin() as conn:
        rows = conn.execute(query, {"ticker": ticker}).fetchall()
    df = pd.DataFrame(rows, columns=["date", "avg_sentiment_score", "avg_relevance_score", "n_articles"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    cutoff = df["date"].max() - pd.DateOffset(months=months)
    return df[df["date"] >= cutoff].reset_index(drop=True)


def get_recent_articles(engine: Engine, ticker: str, limit: int = 30, min_relevance: float = 0.0) -> pd.DataFrame:
    """Últimos `limit` artículos de un ticker (más reciente primero), con
    título, fuente, fecha, sentimiento y relevancia — para el listado de
    titulares de la página por acción.

    `min_relevance` (0-1, ver CONTEXTO.md "Titulares filtrados por
    relevancia"): Alpha Vantage etiqueta con el ticker cualquier artículo
    que lo MENCIONE, aunque el artículo sea en realidad sobre otra
    empresa (ej. "F5 lanza una suite con NVIDIA" aparece bajo NVDA con
    relevancia baja). Por defecto (0.0) no filtra nada — el llamador
    decide el umbral."""
    with engine.begin() as conn:
        result = conn.execute(
            select(dbmod.news_articles)
            .where(dbmod.news_articles.c.ticker == ticker)
            .where(dbmod.news_articles.c.relevance_score >= min_relevance)
            .order_by(dbmod.news_articles.c.date.desc(), dbmod.news_articles.c.time_published.desc())
            .limit(limit)
        )
        rows = result.fetchall()
        columns = result.keys()
    return pd.DataFrame(rows, columns=columns)


def get_market_sentiment_daily(engine: Engine, months: int = 6) -> pd.DataFrame:
    """Sentimiento medio del MERCADO (todos los tickers a la vez) por
    día: media de `avg_sentiment_score` ponderada por nº de artículos de
    cada ticker ese día (no una media simple de medias, para que un
    ticker con 50 artículos pese más que uno con 1) + volumen total de
    artículos. Base para el gráfico de tendencia general."""
    query = text(
        """
        SELECT date, SUM(avg_sentiment_score * n_articles) AS peso_sentimiento,
               SUM(n_articles) AS n_articles
        FROM news_sentiment_daily
        GROUP BY date
        ORDER BY date
        """
    )
    with engine.begin() as conn:
        rows = conn.execute(query).fetchall()
    df = pd.DataFrame(rows, columns=["date", "peso_sentimiento", "n_articles"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df["sentimiento_medio"] = df["peso_sentimiento"] / df["n_articles"]
    cutoff = df["date"].max() - pd.DateOffset(months=months)
    return df.loc[df["date"] >= cutoff, ["date", "sentimiento_medio", "n_articles"]].reset_index(drop=True)


def sentiment_scalar_label(score: float) -> str:
    """Etiqueta en español de un score de sentimiento agregado (-1 a 1).
    Pública (no `_sentiment_scalar_label`): la usan tanto
    `get_market_sentiment_gauge` como las páginas que necesitan etiquetar
    un tono medio propio (p. ej. el resumen dinámico de views/dashboard.py).
    Mismos umbrales que `news._sentiment_label` (no se importa ese módulo
    aquí a propósito — news.py es la capa de ingesta, data_access.py es
    la capa de lectura del dashboard; 5 líneas de umbrales no justifican
    acoplar los dos módulos). Si esos umbrales cambian en news.py, hay que
    replicarlos aquí también."""
    if score <= -0.35:
        return SENTIMENT_LABEL_ES["Bearish"]
    if score <= -0.15:
        return SENTIMENT_LABEL_ES["Somewhat-Bearish"]
    if score < 0.15:
        return SENTIMENT_LABEL_ES["Neutral"]
    if score < 0.35:
        return SENTIMENT_LABEL_ES["Somewhat-Bullish"]
    return SENTIMENT_LABEL_ES["Bullish"]


def get_market_sentiment_gauge(engine: Engine, days: int = 7) -> dict | None:
    """Sentimiento medio del MERCADO (todos los tickers a la vez) de los
    últimos `days` días de calendario CON cobertura, condensado a un único
    escalar. Se construyó originalmente para un medidor semicircular en
    `views/dashboard.py` (2026-08-25), quitado el mismo día tras el
    primer feedback del usuario (ver CONTEXTO.md, "Dashboard unificado
    (mockup)") por ser la única pieza que no se recentraba en la acción
    elegida — se deja esta función aquí, sin usar por ahora, por si hace
    falta un resumen de sentimiento de mercado en otro sitio más
    adelante. Reutiliza `get_market_sentiment_daily` (misma fuente
    ponderada por nº de artículos) en vez de duplicar la consulta SQL:
    solo colapsa la serie diaria a un escalar sobre la ventana reciente.
    None si todavía no hay ninguna noticia guardada de ningún ticker."""
    daily = get_market_sentiment_daily(engine, months=max(1, days // 20 + 1))
    if daily.empty:
        return None
    cutoff = daily["date"].max() - pd.Timedelta(days=days)
    reciente = daily[daily["date"] >= cutoff]
    total_articulos = int(reciente["n_articles"].sum())
    if reciente.empty or total_articulos == 0:
        return None
    sentimiento = float((reciente["sentimiento_medio"] * reciente["n_articles"]).sum() / total_articulos)
    # Cobertura real (auditoría, A3): cuántas acciones aportan artículos a
    # esta cifra. Tras el backfill llegó a ser ~20 de 208 y la tarjeta decía
    # "todo el universo".
    with engine.begin() as conn:
        n_tickers = conn.execute(
            text("SELECT COUNT(DISTINCT ticker) FROM news_articles WHERE date >= :d"),
            {"d": cutoff.date().isoformat()},
        ).scalar() or 0
        total_tickers = conn.execute(_current_tickers_subquery().subquery().select()).fetchall()
    return {
        "sentimiento": sentimiento,
        "n_articles": total_articulos,
        "n_dias": int(reciente["date"].nunique()),
        "n_tickers": int(n_tickers),
        "n_tickers_total": len(total_tickers),
        "label": sentiment_scalar_label(sentimiento),
    }


def get_ticker_sentiment_ranking(engine: Engine, days: int = 7, min_articles: int = 3) -> pd.DataFrame:
    """Sentimiento medio por ticker en los últimos `days` días de
    cobertura real (no días de calendario — si un ticker no tiene
    artículos hoy, cuentan sus últimos días CON artículos), con nombre y
    sector (`stocks`). Filtra tickers con menos de `min_articles`
    artículos en la ventana para que uno o dos artículos sueltos no
    dominen el ranking de "más positivo/negativo" — ver CONTEXTO.md."""
    query = text(
        """
        SELECT ticker, date, avg_sentiment_score, n_articles
        FROM news_sentiment_daily
        """
    )
    with engine.begin() as conn:
        rows = conn.execute(query).fetchall()
        stocks_rows = conn.execute(select(dbmod.stocks)).fetchall()
    df = pd.DataFrame(rows, columns=["ticker", "date", "avg_sentiment_score", "n_articles"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])

    # Para cada ticker, se queda con sus últimos `days` días CON cobertura
    # (no con los últimos `days` días de calendario) — así un ticker que
    # lleva una semana sin noticias no desaparece del ranking solo por
    # mala suerte de timing, sigue reflejando su sentimiento reciente real.
    # `groupby(...).tail(n)` conserva todas las columnas (incluida
    # `ticker`) sin necesitar `apply`, evita el lío de `include_groups`
    # que solo existe desde pandas 2.2.
    recent = df.sort_values("date").groupby("ticker", as_index=False).tail(days)

    agg = recent.groupby("ticker").agg(
        sentimiento=("avg_sentiment_score", "mean"),
        articulos=("n_articles", "sum"),
        ultima_fecha=("date", "max"),
    ).reset_index()
    agg = agg[agg["articulos"] >= min_articles]

    stocks_df = pd.DataFrame(stocks_rows, columns=["ticker", "nombre", "sector", "pais"])
    agg = agg.merge(stocks_df, on="ticker", how="left")
    return agg.sort_values("sentimiento", ascending=False).reset_index(drop=True)


def get_sector_sentiment(engine: Engine, days: int = 30) -> pd.DataFrame:
    """Sentimiento medio por sector, ponderado por nº de artículos,
    usando los últimos `days` días de calendario (a diferencia del
    ranking por ticker, aquí sí se usan días de calendario porque se
    agrega sobre muchos tickers a la vez — la falta de noticias de un
    ticker concreto un día se diluye en el sector)."""
    query = text(
        """
        SELECT nsd.ticker, nsd.date, nsd.avg_sentiment_score, nsd.n_articles, s.sector
        FROM news_sentiment_daily nsd
        JOIN stocks s ON s.ticker = nsd.ticker
        WHERE s.sector IS NOT NULL
        """
    )
    with engine.begin() as conn:
        rows = conn.execute(query).fetchall()
    df = pd.DataFrame(rows, columns=["ticker", "date", "avg_sentiment_score", "n_articles", "sector"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    cutoff = df["date"].max() - pd.DateOffset(days=days)
    df = df[df["date"] >= cutoff]
    if df.empty:
        return df
    df["peso"] = df["avg_sentiment_score"] * df["n_articles"]
    agg = df.groupby("sector").agg(peso=("peso", "sum"), n_articles=("n_articles", "sum")).reset_index()
    agg["sentimiento"] = agg["peso"] / agg["n_articles"]
    return agg.sort_values("sentimiento", ascending=False)[["sector", "sentimiento", "n_articles"]].reset_index(drop=True)


def get_latest_predictions_summary(engine: Engine) -> dict | None:
    """Resumen de las predicciones más recientes (la última `date_predicha`
    guardada, de cualquier `model_version`): cuántas dicen "sube" vs.
    "baja" y la probabilidad media — para un vistazo agregado de "qué
    dice el modelo hoy" sin tener que elegir un ticker. Devuelve None si
    `predictions` está vacía (pipeline no ejecutado todavía)."""
    with engine.begin() as conn:
        max_date = conn.execute(select(dbmod.predictions.c.date_predicha).order_by(
            dbmod.predictions.c.date_predicha.desc()
        ).limit(1)).scalar()
        if max_date is None:
            return None
        rows = conn.execute(
            select(dbmod.predictions).where(dbmod.predictions.c.date_predicha == max_date)
        ).fetchall()
    df = pd.DataFrame(rows, columns=["ticker", "date_predicha", "model_version", "predicted_target_up_down",
                                      "predicted_probability", "predicted_at", "actual_target_up_down"])
    return {
        "date_predicha": max_date,
        "n_total": len(df),
        "n_up": int((df["predicted_target_up_down"] == 1).sum()),
        "n_down": int((df["predicted_target_up_down"] == 0).sum()),
        "avg_probability": float(df["predicted_probability"].mean()),
    }


def feature_importances(bundle: dict) -> pd.DataFrame | None:
    """DataFrame (feature, etiqueta, importancia) ordenado descendente, o
    None si el modelo cargado no expone ninguna medida de importancia
    reconocida (no debería pasar con random_forest/logistic, pero se
    comprueba explícitamente en vez de asumir)."""
    model = bundle["model"]
    names = bundle.get("feature_names") or ALL_FEATURE_NAMES

    # Preferencia (auditoría, M2): importancia por permutación sobre el test,
    # guardada por model.py desde la revisión de auditoría. Mide cuánto baja
    # el acierto fuera de entrenamiento al desordenar cada variable. La MDI de
    # Random Forest queda como alternativa para modelos antiguos.
    perm = bundle.get("permutation_importance")
    if perm:
        df = pd.DataFrame(perm).rename(columns={"mean": "importancia", "std": "desviacion"})
        metodo = "permutacion"
    elif hasattr(model, "feature_importances_"):
        df = pd.DataFrame({"feature": names, "importancia": model.feature_importances_})
        metodo = "mdi"
    elif hasattr(model, "coef_"):
        df = pd.DataFrame({"feature": names, "importancia": abs(model.coef_[0])})
        metodo = "coeficientes"
    else:
        return None

    df["etiqueta"] = [FEATURE_LABELS.get(n, n) for n in df["feature"]]
    df["explicacion"] = [FEATURE_EXPLANATIONS.get(n, "") for n in df["feature"]]
    df.attrs["metodo"] = metodo
    out = df.sort_values("importancia", ascending=False).reset_index(drop=True)
    out.attrs["metodo"] = metodo
    return out


# --- Análisis adicionales sobre el test oficial (2026-08-17) ------------
# Régimen de volatilidad, confianza y comparativa de horizontes reutilizan
# el mismo helper: aplicar el modelo cargado sobre las filas de gold_train
# del PERIODO DE TEST OFICIAL del propio bundle (bundle["test_date_min"]),
# nunca sobre train — evaluar sobre train daría una imagen artificialmente
# buena, ver "Contrato de evaluación" en CONTEXTO.md.

def _test_set_predictions(engine: Engine, bundle: dict) -> pd.DataFrame:
    """Filas de `gold_train` en el periodo de test oficial del bundle
    (`date >= test_date_min`), con la predicción y probabilidad del
    modelo ya calculadas (`pred`, `proba_up`, `acierto`). Devuelve vacío
    si el bundle no tiene `test_date_min` (modelos guardados antes de que
    `model.save_model()` empezara a persistirlo) o si no hay filas con
    target calculado para el horizonte del bundle en ese periodo."""
    horizon = bundle.get("horizon", DEFAULT_PREDICTION_HORIZON)
    target_col = "target_up_down" if horizon == 1 else f"target_up_down_{horizon}d"
    test_date_min = bundle.get("test_date_min")
    if not test_date_min:
        return pd.DataFrame()
    test_date = dt.date.fromisoformat(str(test_date_min))
    with engine.begin() as conn:
        result = conn.execute(dbmod.gold_train.select().where(dbmod.gold_train.c.date >= test_date))
        rows = result.fetchall()
        columns = result.keys()
    df = pd.DataFrame(rows, columns=columns)
    if df.empty:
        return df
    df = df[df[target_col].notna()].copy()
    if df.empty:
        return df
    x = features_for_model(df, bundle.get("feature_names") or ALL_FEATURE_NAMES)
    proba_up = bundle["model"].predict_proba(x)[:, 1]
    df["proba_up"] = proba_up
    df["pred"] = (proba_up >= 0.5).astype(int)
    df["acierto"] = df["pred"] == df[target_col]
    return df


def get_accuracy_by_volatility(engine: Engine, bundle: dict) -> pd.DataFrame:
    """Acierto del modelo (test oficial) segmentado en terciles de
    `volatility_10d` (Baja/Media/Alta) — para comprobar si acierta más en
    mercados tranquilos que en agitados. Terciles calculados sobre el
    propio test set, no sobre un umbral fijo a mano, para que la
    comparación sea siempre sobre grupos de tamaño similar. Vacío si no
    hay suficientes filas para formar 3 grupos con datos distintos."""
    df = _test_set_predictions(engine, bundle)
    if df.empty:
        return pd.DataFrame()
    df = df[df["volatility_10d"].notna()]
    if len(df) < 30:
        return pd.DataFrame()
    try:
        df = df.assign(
            bucket=pd.qcut(df["volatility_10d"], 3, labels=["Baja", "Media", "Alta"], duplicates="drop")
        )
    except ValueError:
        return pd.DataFrame()
    return (
        df.groupby("bucket", observed=True)
        .agg(n=("acierto", "size"), acierto=("acierto", "mean"))
        .reset_index()
    )


def prediction_confidence(predicted_target_up_down: int, predicted_probability: float) -> float:
    """Confianza en la DIRECCIÓN PREDICHA concreta, no en "sube" a secas
    (2026-09-09, ver CONTEXTO.md "Probabilidad mostrada para la dirección
    equivocada"). `predictions.predicted_probability` guarda SIEMPRE
    P(sube) — con `predicted_target_up_down=0` ("baja"), mostrar ese
    valor sin ajustar es literalmente el número equivocado (un 35% de
    P(sube) son en realidad 65% de confianza en "baja"). Antes de esta
    función, la misma fórmula (`p if sube else 1-p`) estaba duplicada en
    `dashboard.py` (dos veces) y `predicciones.py` — consolidada aquí
    como fuente única, mismo criterio que ya usaba
    `get_confidence_accuracy` con `np.maximum(proba_up, 1 - proba_up)`
    (equivalente cuando se conoce ya la clase predicha)."""
    return predicted_probability if predicted_target_up_down == 1 else 1 - predicted_probability


def get_confidence_accuracy(engine: Engine, bundle: dict, threshold: float = 0.6) -> dict | None:
    """Compara el acierto (test oficial) de TODAS las predicciones frente
    al del subconjunto de "alta confianza" (probabilidad a favor de la
    clase predicha >= `threshold`) — para ver si el modelo discrimina
    mejor cuando está más seguro de sí mismo. None si el bundle no tiene
    test set evaluable."""
    df = _test_set_predictions(engine, bundle)
    if df.empty:
        return None
    confianza = np.maximum(df["proba_up"], 1 - df["proba_up"])
    alta = df[confianza >= threshold]
    return {
        "n_total": len(df),
        "acierto_total": float(df["acierto"].mean()),
        "n_alta": len(alta),
        "acierto_alta": float(alta["acierto"].mean()) if not alta.empty else None,
        "pct_alta": len(alta) / len(df) if len(df) else 0.0,
        "threshold": threshold,
    }


def get_calibration_diagnostic(engine: Engine, bundle: dict, n_bins: int = 10) -> dict | None:
    """Diagnóstico de calibración de `predicted_probability` sobre el
    test oficial ACTUAL (2026-09-09, ver CONTEXTO.md "Calibración de
    predicted_probability", feedback de un tutor: no vender la
    probabilidad del modelo como "confianza" sin comprobar calibración).

    Se calcula en vivo sobre `_test_set_predictions()` (mismo patrón que
    `get_accuracy_by_volatility`/`get_confidence_accuracy`, no un
    snapshot congelado del momento del entrenamiento) — así el
    diagnóstico se mantiene fresco a medida que se acumulan más sesiones
    de test reales, sin depender de reentrenar. `model.py` calcula la
    misma métrica en el momento del entrenamiento (se guarda en el propio
    `.joblib` como referencia/reporte de CLI), pero el dashboard usa esta
    versión en vivo como fuente de verdad.

    None si el bundle no tiene test set evaluable (mismo criterio que las
    otras dos funciones de esta familia)."""
    df = _test_set_predictions(engine, bundle)
    if df.empty:
        return None
    target_col = "target_up_down" if bundle.get("horizon", DEFAULT_PREDICTION_HORIZON) == 1 else (
        f"target_up_down_{bundle.get('horizon')}d"
    )
    return calibration_diagnostic(df[target_col].astype(int), df["proba_up"].to_numpy(), n_bins=n_bins)


def get_horizon_comparison(engine: Engine) -> pd.DataFrame:
    """Backtest oficial (modelo vs. los dos baselines) de la versión más
    reciente de CADA horizonte entrenado (día/semana/mes, ver
    `config.PREDICTION_HORIZONS`) — para comparar si hay más señal
    explotable a más plazo. Los horizontes sin ningún modelo entrenado
    todavía aparecen con `disponible=False`, nunca con una fila
    inventada — mismo criterio de honestidad que el resto del dashboard
    (ver CONTEXTO.md)."""
    rows = []
    for horizon, etiqueta in PREDICTION_HORIZONS.items():
        try:
            bundle = load_latest_model(horizon=horizon)
        except FileNotFoundError:
            rows.append({"horizon": horizon, "etiqueta": etiqueta, "disponible": False})
            continue
        if "metrics_model" not in bundle:
            rows.append({"horizon": horizon, "etiqueta": etiqueta, "disponible": False})
            continue
        rows.append({
            "horizon": horizon,
            "etiqueta": etiqueta,
            "disponible": True,
            "model_version": bundle.get("_model_version"),
            "accuracy_modelo": bundle["metrics_model"]["accuracy"],
            "accuracy_mayoritario": bundle["metrics_majority_baseline"]["accuracy"],
            "accuracy_persistencia": bundle["metrics_persistence_baseline"]["accuracy"],
            "test_rows": bundle.get("test_rows"),
        })
    return pd.DataFrame(rows)


def get_market_breadth_history(
    engine: Engine, horizon: int = DEFAULT_PREDICTION_HORIZON, days: int = 90
) -> pd.DataFrame:
    """Evolución histórica del % de tickers que el modelo predijo "sube"
    cada sesión, para un horizonte concreto (deducido de cada
    `model_version` vía `predict.horizon_from_model_version`, igual que
    `get_latest_prediction`/`track_predictions.py`). A diferencia de
    `get_resolved_predictions()`, aquí SÍ se pueden combinar varias
    `model_version` en el mismo cálculo: no se mide acierto (donde
    mezclar versiones sí sería engañoso, ver CONTEXTO.md), solo qué
    fracción predijo "sube" cada día — una lectura de la propia
    inclinación del modelo sobre el mercado, no una métrica de
    rendimiento."""
    with engine.begin() as conn:
        rows = conn.execute(
            select(
                dbmod.predictions.c.date_predicha,
                dbmod.predictions.c.model_version,
                dbmod.predictions.c.predicted_target_up_down,
            )
        ).fetchall()
    df = pd.DataFrame(rows, columns=["date_predicha", "model_version", "predicted_target_up_down"])
    if df.empty:
        return df
    df["horizon_pred"] = df["model_version"].apply(predictmod.horizon_from_model_version)
    df = df[df["horizon_pred"] == horizon]
    if df.empty:
        return df
    resumen = (
        df.groupby("date_predicha")
        .agg(n_total=("predicted_target_up_down", "size"), n_up=("predicted_target_up_down", "sum"))
        .reset_index()
    )
    resumen["pct_up"] = resumen["n_up"] / resumen["n_total"]
    resumen["date_predicha"] = pd.to_datetime(resumen["date_predicha"])
    resumen = resumen.sort_values("date_predicha")
    cutoff = resumen["date_predicha"].max() - pd.Timedelta(days=days)
    return resumen[resumen["date_predicha"] >= cutoff].reset_index(drop=True)


def get_ticker_tape(engine: Engine, n: int = 14) -> list[tuple[str, float, float]]:
    """Último cierre y variación diaria de los `n` primeros tickers de
    `config.TICKERS` (los de mayor capitalización) para la cinta de
    cotizaciones del modo terminal (rediseño 2026-09-17). Solo tickers con
    las dos últimas sesiones cargadas."""
    from config import TICKERS

    tickers = TICKERS[:n]
    query = text(
        """
        SELECT ticker, date, close FROM (
            SELECT ticker, date, close,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
            FROM daily_prices WHERE ticker IN ({})
        ) WHERE rn <= 2
        """.format(", ".join(f":t{i}" for i in range(len(tickers))))
    )
    with engine.begin() as conn:
        rows = conn.execute(query, {f"t{i}": t for i, t in enumerate(tickers)}).fetchall()
    df = pd.DataFrame(rows, columns=["ticker", "date", "close"]).sort_values(["ticker", "date"])
    out = []
    for ticker in tickers:
        sub = df[df["ticker"] == ticker]
        if len(sub) == 2:
            prev, last = float(sub["close"].iloc[0]), float(sub["close"].iloc[1])
            out.append((ticker, last, last / prev - 1))
    return out


def get_tickers_by_sector(engine: Engine) -> pd.DataFrame:
    """Tickers con datos reales (mismo criterio que `list_tickers`: solo
    los que tienen fila en `daily_prices`, para no listar los huérfanos
    de `stocks` como FI/MMC/BK/DFS/HES — ver CONTEXTO.md) junto con su
    sector — base para el filtro por sector de los selectores de ticker
    del dashboard."""
    with engine.begin() as conn:
        rows = conn.execute(
            select(dbmod.stocks.c.ticker, dbmod.stocks.c.nombre, dbmod.stocks.c.sector)
            .where(dbmod.stocks.c.ticker.in_(_current_tickers_subquery()))
            .order_by(dbmod.stocks.c.ticker)
        ).fetchall()
    return pd.DataFrame(rows, columns=["ticker", "nombre", "sector"])
