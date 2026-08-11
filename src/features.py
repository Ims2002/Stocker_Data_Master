"""
src/features.py — cálculo de variables derivadas sobre daily_prices.

Implementa las features cerradas en CONTEXTO.md: `return_1d`, `ma_5`,
`ma_10`, `ma_20`, `volatility_10d`, calculadas sobre `adj_close` (no
`close` — ver CONTEXTO.md, reglas de limpieza) y usando solo datos de días
≤ t (ventanas hacia atrás, `rolling`/`ewm` sin lookahead), para que sea
imposible introducir lookahead por accidente. Ampliadas el 2026-08-06 (ver
CONTEXTO.md, "Ampliación de features") con más indicadores técnicos e
intentar subir el accuracy del modelo por encima de los baselines.

Este módulo es puramente una librería de transformación (recibe y
devuelve DataFrames en memoria) — no lee ni escribe la base de datos. Lo
usa `gold.py` para construir `gold_train`/`gold_inference`.

Todas las columnas nuevas se guardan en su unidad "cruda" (igual que
ma_5/ma_10/ma_20 ya hacían) — es `model.build_feature_matrix` quien las
convierte a variables relativas comparables entre tickers de precio muy
distinto, nunca este módulo (ver docstring de esa función).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Columnas de features que dependen de una ventana temporal — usadas por
# gold.py para saber qué filas todavía no tienen suficiente histórico
# (los primeros días de cada ticker quedan con NaN a propósito). MACD es
# la que más warm-up necesita (26 + 9 - 2 = 33 sesiones), así que en la
# práctica es ella quien determina cuántas filas iniciales se descartan
# por ticker — ver también config.MIN_HISTORY_ROWS_FOR_GOLD.
ROLLING_FEATURE_COLUMNS = [
    "return_1d", "ma_5", "ma_10", "ma_20", "volatility_10d",
    "return_5d", "return_20d", "rsi_14", "macd_line", "macd_signal",
    "price_std_20", "volume_ma_10", "news_sentiment_3d", "news_volume_3d",
]

# Ventanas de los indicadores nuevos — valores estándar de la industria
# (RSI de 14 sesiones, MACD 12/26/9, Bollinger de 20 sesiones y 2
# desviaciones típicas), no ajustados/optimizados para este dataset en
# concreto: son el punto de partida razonable, no un resultado de tuning.
_RSI_WINDOW = 14
_MACD_FAST, _MACD_SLOW, _MACD_SIGNAL = 12, 26, 9
_MACD_WARMUP = _MACD_SLOW + _MACD_SIGNAL - 2  # 33 — ver comentario arriba
_BOLLINGER_WINDOW = 20
_VOLUME_MA_WINDOW = 10

# Ventana del sentimiento de noticias — corta a propósito (3 sesiones, no
# 10/20 como ma_10/ma_20): la relevancia de una noticia decae rápido, y
# la profundidad histórica real disponible tampoco da para una ventana
# larga (NEWS_BACKFILL_MONTHS=6, ver config.py). No es un valor tuneado
# contra el dataset, es un punto de partida razonable — igual que
# _RSI_WINDOW/_MACD_FAST/etc. arriba (ver CONTEXTO.md, "Preparación de
# noticias como feature", 2026-08-08).
_NEWS_SENTIMENT_WINDOW = 3


def _compute_rsi(adj_close: pd.Series, window: int = _RSI_WINDOW) -> pd.Series:
    """RSI (Relative Strength Index) clásico de Wilder, pero con media
    móvil simple en vez de suavizado exponencial — mismo criterio que el
    resto de este módulo (ma_5/10/20 son SMA, no EMA), para no mezclar dos
    estilos de suavizado distintos sin necesidad. Devuelve NaN durante el
    warm-up (< `window` sesiones), igual que las demás rolling features."""
    delta = adj_close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=window, min_periods=window).mean()
    avg_loss = loss.rolling(window=window, min_periods=window).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
    # avg_loss == 0 con avg_gain > 0 (racha alcista pura, ya con historial
    # suficiente): división da inf -> rsi ya sale en 100 por aritmética de
    # punto flotante, no hace falta tratarlo aparte. Caso avg_gain==0 y
    # avg_loss==0 (precio perfectamente plano, rarísimo con datos reales)
    # da 0/0 = NaN — se deja así (excluye esa fila) en vez de inventar un
    # valor, mismo criterio de "NaN antes que un dato fabricado".
    return rsi


def _compute_macd(adj_close: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Línea MACD (EMA rápida - EMA lenta) y su señal (EMA de la línea).
    A diferencia del resto de features de este módulo, usa medias
    exponenciales (`ewm`) porque es la definición estándar de MACD — pero
    se enmascara explícitamente el warm-up (`_MACD_WARMUP` sesiones):
    `ewm` no produce NaN por sí solo como `rolling` (empieza a devolver
    valores desde la primera fila), así que sin este paso quedarían
    "features completas" filas que en realidad no tienen suficiente
    historial detrás — violaría la misma regla de no-lookahead-por-
    ventana-parcial que ya se sigue para ma_5/10/20."""
    ema_fast = adj_close.ewm(span=_MACD_FAST, adjust=False).mean()
    ema_slow = adj_close.ewm(span=_MACD_SLOW, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    macd_signal = macd_line.ewm(span=_MACD_SIGNAL, adjust=False).mean()
    macd_line = macd_line.copy()
    macd_signal = macd_signal.copy()
    macd_line.iloc[:_MACD_WARMUP] = np.nan
    macd_signal.iloc[:_MACD_WARMUP] = np.nan
    return macd_line, macd_signal


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Añade las columnas derivadas a un DataFrame de daily_prices de UN
    solo ticker (no mezclar varios tickers en la misma llamada: las
    ventanas rolling no deben cruzar de un ticker a otro).

    `df` debe tener al menos las columnas `date`, `adj_close` y `volume`.
    Se devuelve una copia ordenada por fecha ascendente con las columnas
    nuevas añadidas; no modifica `df` in-place.
    """
    if df.empty:
        return df.copy()

    out = df.sort_values("date").reset_index(drop=True).copy()

    # Retorno diario sobre adj_close (no close), tal como fija CONTEXTO.md.
    out["return_1d"] = out["adj_close"].pct_change()

    # Medias móviles: min_periods=window a propósito — antes de tener
    # suficiente histórico, la fila queda en NaN en vez de calcularse con
    # una ventana parcial que no sería comparable entre tickers/fechas.
    out["ma_5"] = out["adj_close"].rolling(window=5, min_periods=5).mean()
    out["ma_10"] = out["adj_close"].rolling(window=10, min_periods=10).mean()
    out["ma_20"] = out["adj_close"].rolling(window=20, min_periods=20).mean()

    # Volatilidad: desviación típica de los retornos diarios en ventana de 10.
    out["volatility_10d"] = out["return_1d"].rolling(window=10, min_periods=10).std()

    # --- Ampliación de features (2026-08-06, ver CONTEXTO.md) ---

    # Momentum a más plazos que return_1d — mismo tipo de variable
    # (retorno porcentual), solo cambia la ventana.
    out["return_5d"] = out["adj_close"].pct_change(5)
    out["return_20d"] = out["adj_close"].pct_change(20)

    # RSI: oscilador ya acotado en [0, 100], comparable entre tickers tal
    # cual (no necesita normalización adicional en build_feature_matrix).
    out["rsi_14"] = _compute_rsi(out["adj_close"])

    # MACD: se guardan la línea y la señal en unidades de precio (como
    # ma_5/10/20) — build_feature_matrix las convierte al histograma
    # normalizado por precio para que sea comparable entre tickers.
    out["macd_line"], out["macd_signal"] = _compute_macd(out["adj_close"])

    # Bandas de Bollinger: se guarda solo la desviación típica de 20
    # sesiones (price_std_20) porque el centro de las bandas ES ma_20,
    # que ya se calcula arriba — evita duplicar la misma media dos veces.
    out["price_std_20"] = out["adj_close"].rolling(
        window=_BOLLINGER_WINDOW, min_periods=_BOLLINGER_WINDOW
    ).std()

    # Volumen medio de 10 sesiones — permite un volumen RELATIVO
    # (volumen de hoy vs. su propia media reciente) en vez de solo
    # log(volumen), que no es comparable entre una acción muy líquida y
    # una poco líquida (limitación ya documentada en model.py).
    out["volume_ma_10"] = out["volume"].rolling(
        window=_VOLUME_MA_WINDOW, min_periods=_VOLUME_MA_WINDOW
    ).mean()

    # Día de la semana (0=lunes … 4=viernes): no depende de histórico
    # previo, por eso no está en ROLLING_FEATURE_COLUMNS ni puede venir en
    # NaN. `pd.to_datetime` por robustez: `date` puede llegar como
    # `datetime.date` (SQLAlchemy) según quién llame a esta función.
    out["day_of_week"] = pd.to_datetime(out["date"]).dt.dayofweek

    return out


def has_complete_features(df: pd.DataFrame) -> pd.Series:
    """Máscara booleana: True en las filas donde TODAS las features
    rolling ya están calculadas (no NaN por falta de histórico previo)."""
    return df[ROLLING_FEATURE_COLUMNS].notna().all(axis=1)


def attach_news_features(
    feat: pd.DataFrame, news: pd.DataFrame, window: int = _NEWS_SENTIMENT_WINDOW
) -> pd.DataFrame:
    """Añade `news_sentiment_3d`/`news_volume_3d` a `feat` (salida de
    `compute_features()`, una fila por sesión de UN ticker, orden
    ascendente por fecha).

    `news` es el resultado de `news_sentiment_daily` (vista agregada
    sobre `news_articles`, ver `db.py`) para ESE MISMO ticker: solo trae
    fila para los días en los que hubo al menos un artículo — la mayoría
    de sesiones no tendrán fila, sobre todo fuera de la ventana de
    `NEWS_BACKFILL_MONTHS` (config.py) o en tickers que el backfill de
    noticias todavía no ha cubierto (backfill en curso, ver CONTEXTO.md).
    Debe traer como mínimo las columnas `date`, `avg_sentiment_score` y
    `n_articles`; puede venir vacío (DataFrame sin filas) sin problema.

    "Sin noticias ese día" se trata como sentimiento neutro (0) y volumen
    cero, no como NaN — mismo criterio ya establecido para
    `bb_pct_b`/`relative_volume` (CONTEXTO.md, "Ampliación de features"):
    es un valor real e informativo ("no hubo cobertura"), no un dato
    ausente que haya que enmascarar.

    El suavizado a `window` sesiones usa `.rolling()` SIN `min_periods`,
    igual que el resto de indicadores de este módulo (nunca una ventana
    parcial) — como la serie diaria ya no tiene NaN tras el fillna(0),
    esto solo deja en NaN las primeras `window - 1` filas de TODO el
    histórico del ticker (calentamiento estándar, igual que ma_5/rsi_14),
    no introduce huecos nuevos en mitad de la serie."""
    out = feat.copy()

    if news is None or news.empty:
        daily_sentiment = pd.Series(0.0, index=out.index)
        daily_volume = pd.Series(0.0, index=out.index)
    else:
        merged = feat[["date"]].merge(
            news[["date", "avg_sentiment_score", "n_articles"]],
            on="date",
            how="left",
        )
        daily_sentiment = merged["avg_sentiment_score"].fillna(0.0)
        daily_volume = merged["n_articles"].fillna(0.0)

    out["news_sentiment_3d"] = daily_sentiment.rolling(window=window, min_periods=window).mean().to_numpy()
    out["news_volume_3d"] = daily_volume.rolling(window=window, min_periods=window).sum().to_numpy()

    return out
