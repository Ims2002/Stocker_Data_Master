"""
src/db.py — esquema de la base de datos (SQLite) y su inicialización.

Implementa, tal cual está cerrado en CONTEXTO.md, las tablas de la capa
processed (`daily_prices`) y de la capa gold (`gold_train`, `gold_inference`,
`predictions`), más la tabla de dimensión `stocks`. El esquema es multi-
ticker: todas las tablas llevan `ticker` como parte de la PK, y `gold_train`/
`gold_inference` se comparten entre todos los tickers configurados (ya no
llevan el ticker en el nombre — se renombraron desde `gold_aapl_train` /
`gold_aapl_inference` al ampliar el alcance de una sola acción a un
universo de varias, ver CONTEXTO.md).

La capa gold se divide físicamente en tres tablas — no una sola con el
target a NULL en la última fila — precisamente para que sea estructuralmente
imposible que una etiqueta se cuele como feature:

- `gold_train` SÍ tiene `close_next_day` y `target_up_down` (NOT NULL).
- `gold_inference` NO tiene esas columnas — ni siquiera vacías. No existen
  en el esquema, así que el modelo no puede recibirlas por error.

Motor: SQLite en el MVP (ver CONTEXTO.md; migración natural a PostgreSQL
si se escala más allá de ~50 tickers — ningún módulo debe asumir SQLite
fuera de `get_engine()`/`DB_URL`).

Uso:
    python db.py     # crea (si no existen) todas las tablas en config.DB_PATH
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    text,
)
from sqlalchemy.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DB_URL  # noqa: E402

metadata = MetaData()


def _gold_feature_columns() -> list[Column]:
    """Columnas de features compartidas por gold_train y gold_inference.
    Se devuelve una lista NUEVA de objetos Column en cada llamada porque
    SQLAlchemy no permite reutilizar la misma instancia de Column en dos
    tablas distintas.

    open/close/adj_close/volume son obligatorios; high/low son deseables
    (no se usan como feature en la primera versión del modelo, ver
    CONTEXTO.md), de ahí que sean nullable.

    Ampliación de features (2026-08-06, ver CONTEXTO.md "Ampliación de
    features"): rsi_14, macd_line, macd_signal, price_std_20, return_5d,
    return_20d, volume_ma_10 son valores "crudos" (mismo criterio que
    ma_5/ma_10/ma_20: se guardan en su unidad nativa y es
    `model.build_feature_matrix` quien los convierte a variables relativas
    comparables entre tickers, nunca aquí). day_of_week no depende de
    ventana histórica (se calcula directo de `date`), por eso es la única
    de este grupo que no puede quedar NULL.
    """
    return [
        Column("open", Float, nullable=False),
        Column("high", Float, nullable=True),
        Column("low", Float, nullable=True),
        Column("close", Float, nullable=False),
        Column("adj_close", Float, nullable=False),
        Column("volume", Integer, nullable=False),
        Column("return_1d", Float, nullable=True),
        Column("ma_5", Float, nullable=True),
        Column("ma_10", Float, nullable=True),
        Column("ma_20", Float, nullable=True),
        Column("volatility_10d", Float, nullable=True),
        Column("return_5d", Float, nullable=True),
        Column("return_20d", Float, nullable=True),
        Column("rsi_14", Float, nullable=True),
        Column("macd_line", Float, nullable=True),
        Column("macd_signal", Float, nullable=True),
        Column("price_std_20", Float, nullable=True),
        Column("volume_ma_10", Float, nullable=True),
        Column("day_of_week", Integer, nullable=False),
    ]


# --- stocks (dimensión) -----------------------------------------------------
stocks = Table(
    "stocks",
    metadata,
    Column("ticker", String, primary_key=True),
    Column("nombre", String, nullable=True),
    Column("sector", String, nullable=True),
    Column("pais", String, nullable=True),
)

# --- daily_prices (processed) — histórico limpio y tipado -------------------
daily_prices = Table(
    "daily_prices",
    metadata,
    Column("ticker", String, primary_key=True),
    Column("date", Date, primary_key=True),
    Column("open", Float, nullable=False),
    Column("high", Float, nullable=True),
    Column("low", Float, nullable=True),
    Column("close", Float, nullable=False),
    Column("adj_close", Float, nullable=False),
    Column("volume", Integer, nullable=False),
    ForeignKeyConstraint(["ticker"], ["stocks.ticker"]),
    CheckConstraint("open > 0", name="ck_daily_prices_open_positive"),
    CheckConstraint("close > 0", name="ck_daily_prices_close_positive"),
    CheckConstraint("adj_close > 0", name="ck_daily_prices_adj_close_positive"),
    CheckConstraint("volume >= 0", name="ck_daily_prices_volume_non_negative"),
)

# --- gold_train (gold) — SOLO para entrenar/evaluar, todos los tickers -----
gold_train = Table(
    "gold_train",
    metadata,
    Column("ticker", String, primary_key=True),
    Column("date", Date, primary_key=True),
    *_gold_feature_columns(),
    # Labels — solo existen en train, nunca en inference.
    Column("close_next_day", Float, nullable=False),
    Column("target_up_down", Integer, nullable=False),
    ForeignKeyConstraint(["ticker", "date"], ["daily_prices.ticker", "daily_prices.date"]),
    CheckConstraint("target_up_down IN (0, 1)", name="ck_gold_train_target_binary"),
)

# --- gold_inference (gold) — SOLO para predecir "mañana", todos los tickers -
gold_inference = Table(
    "gold_inference",
    metadata,
    Column("ticker", String, primary_key=True),
    Column("date", Date, primary_key=True),
    *_gold_feature_columns(),
    # Intencionadamente SIN close_next_day ni target_up_down: no deben
    # existir ni siquiera como columnas vacías (ver CONTEXTO.md).
    ForeignKeyConstraint(["ticker", "date"], ["daily_prices.ticker", "daily_prices.date"]),
)

# --- predictions (obligatoria, no opcional) ---------------------------------
predictions = Table(
    "predictions",
    metadata,
    Column("ticker", String, primary_key=True),
    Column("date_predicha", Date, primary_key=True),
    Column("model_version", String, primary_key=True),
    Column("predicted_target_up_down", Integer, nullable=False),
    Column("predicted_probability", Float, nullable=False),
    Column("predicted_at", DateTime, nullable=False),
    # Se rellena a posteriori, cuando el cierre real de date_predicha ya
    # existe en daily_prices — por eso es nullable y no lleva FK propia.
    Column("actual_target_up_down", Integer, nullable=True),
    ForeignKeyConstraint(["ticker"], ["stocks.ticker"]),
    CheckConstraint("predicted_target_up_down IN (0, 1)", name="ck_predictions_predicted_binary"),
    CheckConstraint(
        "actual_target_up_down IS NULL OR actual_target_up_down IN (0, 1)",
        name="ck_predictions_actual_binary",
    ),
)


# --- news_articles (processed) — sentimiento por artículo x ticker --------
# Fuente: Alpha Vantage NEWS_SENTIMENT (decisión explícita del usuario,
# 2026-07-30, ver CONTEXTO.md — comparada con Finnhub y yfinance.news). Un
# mismo artículo puede mencionar varios tickers con distinto sentimiento
# cada uno, de ahí que la PK sea (ticker, url) y no solo `url`: cada fila es
# "este artículo dice esto sobre este ticker en concreto", no el artículo
# en sí. El upsert por (ticker, url) hace que ventanas de fechas que se
# solapan entre sí (o entre backfill y actualización diaria) no dupliquen
# filas — dedupe natural, sin lógica aparte.
news_articles = Table(
    "news_articles",
    metadata,
    Column("ticker", String, primary_key=True),
    Column("url", String, primary_key=True),
    Column("date", Date, nullable=False),
    Column("title", String, nullable=True),
    Column("source", String, nullable=True),
    Column("relevance_score", Float, nullable=False),
    Column("ticker_sentiment_score", Float, nullable=False),
    Column("ticker_sentiment_label", String, nullable=False),
    Column("time_published", String, nullable=False),
    Column("fetched_at", DateTime, nullable=False),
    ForeignKeyConstraint(["ticker"], ["stocks.ticker"]),
    CheckConstraint(
        "relevance_score >= 0 AND relevance_score <= 1",
        name="ck_news_articles_relevance_range",
    ),
    CheckConstraint(
        "ticker_sentiment_score >= -1 AND ticker_sentiment_score <= 1",
        name="ck_news_articles_sentiment_range",
    ),
)

# --- news_backfill_progress — qué (lote de tickers, ventana de fechas) ya
# se ha pedido a la API. Necesaria porque el free tier de Alpha Vantage (25
# peticiones/día) no permite completar el backfill de 208 tickers x ~2 años
# en una sola ejecución — sin esto, cada ejecución repetiría desde el
# principio en vez de continuar donde se quedó la anterior.
#
# La identidad es `batch_key` (hash de los tickers exactos de ese lote), NO
# `batch_id` (posición del lote en la lista). Motivo real, detectado el
# 2026-07-31: al bajar config.NEWS_TICKERS_PER_CALL de 50 a 10, el lote
# "batch_id=4" pasó de significar "los últimos 8 tickers de config.TICKERS"
# a significar "los tickers 40-49" — un conjunto totalmente distinto. Con
# `batch_id` como identidad, las 4 filas ya marcadas como hechas bajo la
# config antigua se habrían seguido leyendo como "hecho" para el batch_id=4
# NUEVO, saltándose para siempre esos tickers sin que nadie lo notara. Un
# hash de los tickers del lote no sufre este problema: si la composición
# del lote cambia, simplemente genera una clave distinta y no colisiona con
# progreso de otra composición (ver `news._batch_key()`).
news_backfill_progress = Table(
    "news_backfill_progress",
    metadata,
    Column("batch_key", String, primary_key=True),
    Column("window_start", Date, primary_key=True),
    Column("batch_id", Integer, nullable=False),  # solo informativo (logs), no es la clave
    Column("window_end", Date, nullable=False),
    Column("completed_at", String, nullable=False),
)


def _migrate_gold_tables(engine: Engine) -> None:
    """Si `gold_train`/`gold_inference` existen con el esquema antiguo (sin
    las columnas de la ampliación de features de 2026-08-06 — rsi_14,
    macd_line, etc., ver `_gold_feature_columns()`), se eliminan y se
    recrean vacías. Seguro de hacer: a diferencia de `news_articles` (datos
    caros de volver a pedir a una API con cuota limitada), gold_train/
    gold_inference son 100% derivadas de `daily_prices` y `gold.py` las
    recalcula por completo (delete+insert) en cada ejecución — no hay nada
    que perder que no se regenere solo con `python gold.py`."""
    with engine.begin() as conn:
        for table_name in ("gold_train", "gold_inference"):
            cols = [row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})"))]
            if cols and "rsi_14" not in cols:
                print(
                    f"[db] {table_name} con esquema antiguo (sin las features nuevas) — se recrea "
                    "vacía (ver CONTEXTO.md, 'Ampliación de features', 2026-08-06). "
                    "Ejecuta `python gold.py` después para repoblarla.",
                    file=sys.stderr,
                )
                conn.execute(text(f"DROP TABLE {table_name}"))


def _migrate_news_backfill_progress(engine: Engine) -> None:
    """Si `news_backfill_progress` existe con el esquema antiguo (PK =
    batch_id, sin columna batch_key), se elimina y se recrea vacía — ver el
    comentario sobre `batch_key` arriba. Perder esas filas es seguro: como
    mucho se repiten unas pocas llamadas ya hechas (news_articles dedupe
    por (ticker, url)), nunca se pierde o corrompe nada."""
    with engine.begin() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(news_backfill_progress)"))]
        if cols and "batch_key" not in cols:
            print(
                "[db] news_backfill_progress con esquema antiguo (sin batch_key) — se recrea vacía "
                "(ver CONTEXTO.md, incidente 2026-07-31).",
                file=sys.stderr,
            )
            conn.execute(text("DROP TABLE news_backfill_progress"))


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """SQLite ignora las FOREIGN KEY constraints salvo que se activen por
    conexión — sin esto, las ForeignKeyConstraint definidas arriba no se
    harían cumplir en tiempo de ejecución."""

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_engine() -> Engine:
    engine = create_engine(DB_URL)
    if DB_URL.startswith("sqlite"):
        _enable_sqlite_foreign_keys(engine)
    return engine


def init_db(engine: Engine | None = None) -> Engine:
    """Crea todas las tablas si no existen. No destruye ni modifica tablas
    ya existentes (checkfirst=True).

    También crea la vista `news_sentiment_daily`, que agrega
    `news_articles` por (ticker, date) — NO es una tabla física (mismo
    principio que impide recomponer gold_train/gold_inference "por
    comodidad": una vista no puede quedar nunca desincronizada de la tabla
    que agrega, porque se recalcula en cada SELECT)."""
    engine = engine or get_engine()
    _migrate_news_backfill_progress(engine)
    _migrate_gold_tables(engine)
    metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE VIEW IF NOT EXISTS news_sentiment_daily AS
                SELECT
                    ticker,
                    date,
                    AVG(ticker_sentiment_score) AS avg_sentiment_score,
                    AVG(relevance_score) AS avg_relevance_score,
                    COUNT(*) AS n_articles
                FROM news_articles
                GROUP BY ticker, date
                """
            )
        )
    return engine


def seed_stocks(engine: Engine | None = None, tickers: list[str] | None = None) -> None:
    """Inserta una fila en `stocks` por cada ticker de `tickers` (por
    defecto, config.TICKERS), si no existe ya (idempotente).

    Necesario antes de poder insertar nada en `daily_prices`, `gold_*` o
    `predictions`: todas llevan FK a `stocks.ticker`, y con
    `PRAGMA foreign_keys=ON` (activado en get_engine()) SQLite rechazará
    esos inserts si el ticker no existe primero en `stocks`.

    Se acepta `tickers` explícito (no solo config.TICKERS) para que
    `load.py` pueda sembrar el ticker concreto que está cargando aunque no
    esté todavía en config.TICKERS (p. ej. al invocar `load.py TICKER` a
    mano) — sin esto, ese insert fallaría por la FK.
    """
    if tickers is None:
        from config import TICKERS

        tickers = TICKERS

    engine = engine or get_engine()
    with engine.begin() as conn:
        for ticker in tickers:
            existing = conn.execute(
                stocks.select().where(stocks.c.ticker == ticker)
            ).first()
            if existing is None:
                conn.execute(stocks.insert().values(ticker=ticker))
                print(f"[db] stocks: añadido {ticker}", file=sys.stderr)


def drop_legacy_gold_tables(engine: Engine | None = None) -> None:
    """Migración puntual: elimina las tablas `gold_aapl_train` /
    `gold_aapl_inference` (nombre antiguo, de cuando el proyecto era de un
    solo ticker) si quedaron creadas de una ejecución anterior de db.py.
    Seguro de ejecutar aunque no existan, y no toca `daily_prices` ni
    ninguna otra tabla — esas tablas legacy están vacías (nada las llenaba
    todavía) así que no hay datos que perder.
    """
    engine = engine or get_engine()
    from sqlalchemy import text

    with engine.begin() as conn:
        for legacy_name in ("gold_aapl_train", "gold_aapl_inference"):
            conn.execute(text(f"DROP TABLE IF EXISTS {legacy_name}"))
            print(f"[db] eliminada tabla legacy (si existía): {legacy_name}", file=sys.stderr)


if __name__ == "__main__":
    eng = init_db()
    seed_stocks(eng)
    drop_legacy_gold_tables(eng)
    print(f"[db] esquema listo en {eng.url}", file=sys.stderr)
    for table_name in metadata.tables:
        print(f"  - {table_name}", file=sys.stderr)
