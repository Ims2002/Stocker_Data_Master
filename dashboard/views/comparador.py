"""
dashboard/views/comparador.py — comparador de gráficos entre dos acciones.

Página nueva (2026-09-05, ver CONTEXTO.md "Comparador de gráficos: dos
acciones en un mismo gráfico") — independiente de predicción/backtest,
100% de solo lectura sobre daily_prices (ver data_access.py, y la regla
"el dashboard nunca escribe nada" de dashboard/README.md). Permite elegir
dos tickers cualquiera del universo y comparar su evolución de precio en
un mismo gráfico.

Por qué normalizado y no precio bruto: comparar dos acciones con escalas
muy distintas (p. ej. NVDA ~180$ vs. una acción de 20$) en el mismo eje Y
no es legible — una aplasta a la otra. Se indexa el cierre de cada
ticker a 100 en el primer día de la ventana seleccionada (equivalente a
% de cambio desde el inicio), que es el estándar para gráficos de
comparación multi-activo.

v1 (esta entrega, decisión del usuario 2026-09-05): SOLO precio
normalizado. La estructura de este archivo está pensada para añadir,
más adelante y sin reescribir:
  - Panel técnico comparado (RSI, volatilidad...) — hueco dejado en
    `_render_technical_panel()`, usaría `da.get_gold_train_for_ticker()`
    (ya calcula rsi_14, volatility_10d, macd_line, etc. por ticker).
  - KPI de correlación entre las dos series — hueco dejado en
    `_render_correlation_kpi()`, sobre los retornos diarios de la misma
    ventana ya cargada.
Ninguna de las dos se implementa ahora (NotImplementedError a propósito
si se llaman) — ver CONTEXTO.md para el porqué del alcance.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_access as da  # noqa: E402

engine = da.get_engine()

_COLOR_A = "#1D4ED8"  # azul — paleta de ui.py
_COLOR_B = "#0B0F19"  # negro — paleta de ui.py (más contraste que el azul marino junto al azul principal)


@st.cache_data(ttl=300)
def _tickers() -> list[str]:
    return da.list_tickers(engine)


@st.cache_data(ttl=300)
def _tickers_by_sector() -> pd.DataFrame:
    return da.get_tickers_by_sector(engine)


def _ticker_selector(
    label: str,
    key_prefix: str,
    tickers: list[str],
    meta_tickers: pd.DataFrame,
    default_ticker: str | None,
) -> str:
    """Selector sector→ticker para UNA de las dos acciones a comparar.
    Se llama dos veces (una por acción, key_prefix distinto para no
    colisionar en el estado de Streamlit) — mismo patrón de filtro por
    sector que dashboard/views/predicciones.py, factorizado aquí para no
    duplicarlo dos veces en la misma página."""
    sectores = ["Todos"] + sorted(meta_tickers["sector"].dropna().unique().tolist())
    col_s, col_t = st.columns([1, 1.4])
    with col_s:
        sector = st.selectbox(f"Sector — {label}", sectores, index=0, key=f"{key_prefix}_sector")
    tickers_filtrados = (
        tickers
        if sector == "Todos"
        else [t for t in tickers if t in set(meta_tickers.loc[meta_tickers["sector"] == sector, "ticker"])]
    ) or tickers
    if default_ticker in tickers_filtrados:
        idx = tickers_filtrados.index(default_ticker)
    else:
        idx = da.default_ticker_index(tickers_filtrados)
    with col_t:
        ticker = st.selectbox(f"Ticker — {label}", tickers_filtrados, index=idx, key=f"{key_prefix}_ticker")
    return ticker


def _ticker_header(ticker: str) -> None:
    meta = da.get_ticker_metadata(engine, ticker)
    logo_url = da.ticker_logo_url(ticker, size=32)
    col_logo, col_meta = st.columns([1, 8])
    if logo_url:
        with col_logo:
            st.image(logo_url, width=32)
    with col_meta:
        st.caption(f"**{meta.get('nombre') or ticker}** · {meta.get('sector') or 'sector desconocido'}")


def _normalize_to_100(df: pd.DataFrame) -> pd.Series:
    base = df["close"].iloc[0]
    return (df["close"] / base) * 100.0


def _render_price_chart(df_a: pd.DataFrame, ticker_a: str, df_b: pd.DataFrame, ticker_b: str) -> None:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df_a["date"],
            y=_normalize_to_100(df_a),
            mode="lines",
            name=ticker_a,
            line=dict(color=_COLOR_A, width=1.5),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df_b["date"],
            y=_normalize_to_100(df_b),
            mode="lines",
            name=ticker_b,
            line=dict(color=_COLOR_B, width=1.5),
        )
    )
    fig.update_layout(
        height=500,
        margin=dict(l=10, r=10, t=30, b=10),
        yaxis_title="Evolución normalizada (base 100 al inicio de la ventana)",
        xaxis_title=None,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
    )
    with st.container(border=True):
        st.plotly_chart(fig, width="stretch")


def _render_technical_panel(df_a: pd.DataFrame, ticker_a: str, df_b: pd.DataFrame, ticker_b: str) -> None:
    """Punto de extensión futuro (no implementado en v1): panel de
    indicadores técnicos comparados (RSI, volatilidad...) usando
    `da.get_gold_train_for_ticker()`, que ya calcula rsi_14,
    volatility_10d, macd_line, etc. por ticker — no haría falta
    recalcular nada, solo pedir esas columnas para los dos tickers y
    graficarlas igual que el precio. Ver CONTEXTO.md, "Comparador de
    gráficos", para el porqué de dejarlo fuera de esta entrega."""
    raise NotImplementedError("Panel técnico comparado: pendiente, fuera del alcance de v1.")


def _render_correlation_kpi(df_a: pd.DataFrame, ticker_a: str, df_b: pd.DataFrame, ticker_b: str) -> None:
    """Punto de extensión futuro (no implementado en v1): KPI de
    correlación entre las dos series sobre la ventana ya cargada, p. ej.
    `df_a["close"].pct_change().corr(df_b["close"].pct_change())`. No
    necesita datos adicionales a los que ya carga esta página — solo
    mostrar el número. Ver CONTEXTO.md, "Comparador de gráficos"."""
    raise NotImplementedError("KPI de correlación: pendiente, fuera del alcance de v1.")


st.title("Comparador de gráficos")
st.caption(
    "Compara la evolución de precio de dos acciones en un mismo gráfico, "
    "independientemente de las predicciones del modelo. El precio se muestra "
    "normalizado (base 100 al inicio de la ventana) para poder comparar "
    "acciones con escalas de precio muy distintas."
)

tickers = _tickers()
meta_tickers = _tickers_by_sector()

col_a, col_b, col_m = st.columns([2, 2, 1])
with col_a:
    st.markdown("**Acción A**")
    ticker_a = _ticker_selector("A", "cmp_a", tickers, meta_tickers, default_ticker=None)
with col_b:
    st.markdown("**Acción B**")
    fallback_b = next((t for t in tickers if t != ticker_a), ticker_a)
    ticker_b = _ticker_selector("B", "cmp_b", tickers, meta_tickers, default_ticker=fallback_b)
with col_m:
    st.markdown("**Ventana**")
    meses = st.selectbox("Meses", [3, 6, 12, 24, 36], index=2, key="cmp_meses")

if ticker_a == ticker_b:
    st.info("Elige dos tickers distintos para compararlos.")
    st.stop()

col_h1, col_h2 = st.columns(2)
with col_h1:
    _ticker_header(ticker_a)
with col_h2:
    _ticker_header(ticker_b)

df_a = da.get_price_history(engine, ticker_a, months=meses)
df_b = da.get_price_history(engine, ticker_b, months=meses)

if df_a.empty or df_b.empty:
    st.warning("No hay datos suficientes para uno de los tickers seleccionados.")
    st.stop()

_render_price_chart(df_a, ticker_a, df_b, ticker_b)
