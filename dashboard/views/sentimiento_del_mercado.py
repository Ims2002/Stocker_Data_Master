"""
dashboard/views/sentimiento_del_mercado.py — cuadro de mando de
sentimiento agregado de TODO el universo de tickers (ver CONTEXTO.md,
"Cuadros de mando de noticias y sentimiento", 2026-08-13). Puramente
informativo — no es una señal usada por el modelo con fiabilidad
demostrada (ver CONTEXTO.md, "¿Ayuda el sentimiento de noticias a
acertar más?", 2026-08-13: no se encontró correlación real con la
dirección del precio). Vive en views/, no pages/ — ver nota en
views/inicio.py sobre por qué.
"""

from __future__ import annotations

import sys
from pathlib import Path

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_access as da  # noqa: E402

st.title("Sentimiento del mercado")
st.caption(
    "Tono de las noticias de todo el universo de acciones seguido, agregado día a día — para hacerte una "
    "idea del ambiente general, no para predecir nada. El backfill de noticias sigue en marcha, así que "
    "la cobertura aumenta cada día (ver abajo)."
)

engine = da.get_engine()


@st.cache_data(ttl=300)
def _coverage():
    return da.news_coverage_status(engine)


@st.cache_data(ttl=300)
def _market_daily():
    return da.get_market_sentiment_daily(engine)


@st.cache_data(ttl=300)
def _ranking():
    return da.get_ticker_sentiment_ranking(engine)


@st.cache_data(ttl=300)
def _sector():
    return da.get_sector_sentiment(engine)


coverage = _coverage()

with st.container(border=True):
    col1, col2, col3 = st.columns(3)
    col1.metric(
        "Tickers con cobertura",
        f"{coverage['tickers_cubiertos']}/{coverage['tickers_totales']}",
    )
    col2.metric("Artículos guardados", f"{coverage['articulos_totales']:,}".replace(",", "."))
    pendientes = coverage["tickers_totales"] - coverage["tickers_cubiertos"]
    col3.metric("Tickers pendientes de backfill", pendientes)

if coverage["tickers_cubiertos"] < coverage["tickers_totales"]:
    st.info(
        "El backfill de noticias todavía no cubre todo el universo — los tickers sin cobertura simplemente "
        "no aparecen en los rankings de abajo, no cuentan como \"neutros\". Ver CONTEXTO.md, "
        "\"Rediseño de noticias\".",
        icon="🕒",
    )

st.divider()

market = _market_daily()
if market.empty:
    st.info("Todavía no hay noticias guardadas de ningún ticker. Vuelve en unos días.", icon="🕒")
    st.stop()

# --- Tendencia de mercado: tono medio ponderado (línea) + volumen (barras) ---
st.subheader("Tendencia del mercado")
fig = make_subplots(specs=[[{"secondary_y": True}]])
fig.add_trace(
    go.Bar(x=market["date"], y=market["n_articles"], name="Nº de artículos (todo el mercado)", marker_color="#EEF0F2"),
    secondary_y=False,
)
fig.add_trace(
    go.Scatter(
        x=market["date"], y=market["sentimiento_medio"], name="Tono medio del mercado", mode="lines",
        line=dict(color="#1D4ED8", width=2),
    ),
    secondary_y=True,
)
fig.add_hline(y=0, line_dash="dot", line_color="#9CA3AF", secondary_y=True)
fig.update_yaxes(title_text="Nº de artículos", secondary_y=False, showgrid=False)
fig.update_yaxes(title_text="Tono medio (-1 a +1)", secondary_y=True, range=[-1, 1])
fig.update_layout(
    height=420, margin=dict(l=10, r=10, t=20, b=10),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
)
with st.container(border=True):
    st.plotly_chart(fig, width="stretch")
st.caption(
    "Ponderado por nº de artículos de cada ticker cada día, no una media simple de medias — así una acción "
    "muy mediática pesa más que una con un único artículo suelto."
)

st.divider()

# --- Ranking: más positivos / más negativos ---
st.subheader("Más positivos y más negativos ahora mismo")
ranking = _ranking()
if ranking.empty:
    st.info("Todavía no hay suficientes tickers con cobertura para un ranking fiable.", icon="🕒")
else:
    top_n = 10
    positivos = ranking.head(top_n).sort_values("sentimiento")
    negativos = ranking.tail(top_n).sort_values("sentimiento")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Tono más positivo**")
        fig_pos = px.bar(
            positivos, x="sentimiento", y="ticker", orientation="h",
            hover_data={"nombre": True, "articulos": True},
            color_discrete_sequence=["#2ca02c"],
        )
        fig_pos.update_layout(
            height=340, margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title="Tono medio (últimos días con cobertura)", yaxis_title=None,
            plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
        )
        st.plotly_chart(fig_pos, width="stretch")
    with col_b:
        st.markdown("**Tono más negativo**")
        fig_neg = px.bar(
            negativos, x="sentimiento", y="ticker", orientation="h",
            hover_data={"nombre": True, "articulos": True},
            color_discrete_sequence=["#d62728"],
        )
        fig_neg.update_layout(
            height=340, margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title="Tono medio (últimos días con cobertura)", yaxis_title=None,
            plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
        )
        st.plotly_chart(fig_neg, width="stretch")
    st.caption(
        "Solo tickers con al menos unos pocos artículos recientes (para que uno o dos artículos sueltos no "
        "distorsionen el ranking) — ver CONTEXTO.md."
    )

st.divider()

# --- Sentimiento por sector ---
st.subheader("Por sector (últimos 30 días)")
sector = _sector()
if sector.empty:
    st.info("Todavía no hay suficiente cobertura para desglosar por sector.", icon="🕒")
else:
    fig_sector = px.bar(
        sector, x="sentimiento", y="sector", orientation="h",
        hover_data={"n_articles": True},
        color_discrete_sequence=["#1D4ED8"],
    )
    fig_sector.update_layout(
        height=420, margin=dict(l=10, r=10, t=10, b=10),
        yaxis=dict(autorange="reversed"),
        xaxis_title="Tono medio", yaxis_title=None,
        plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
    )
    st.plotly_chart(fig_sector, width="stretch")
