"""
dashboard/views/sentimiento_por_accion.py — cuadro de mando de noticias y
sentimiento de UNA acción concreta (ver CONTEXTO.md, "Cuadros de mando de
noticias y sentimiento", 2026-08-13). Puramente informativo — NO es una
página de rendimiento del modelo: la investigación del mismo día
(CONTEXTO.md, "¿Ayuda el sentimiento de noticias a acertar más?") no
encontró que esta señal ayude a predecir la dirección del precio, así que
esta página no debe insinuar que sí. Vive en views/, no pages/ — ver nota
en views/inicio.py sobre por qué.

REINCORPORADA A LA NAVEGACIÓN 2026-09-08 (ver CONTEXTO.md, "Quitar
'¿Funciona de verdad?' y profundizar en noticias desde el Dashboard"): se
había quitado del v1 (2026-08-27) porque el backfill de Alpha Vantage
todavía estaba en marcha — ya está completo (208/208 tickers). Se vuelve
a registrar porque el Dashboard solo enseña un recorte de 4 titulares en
su caja de noticias y el usuario pidió poder profundizar desde ahí: el
botón "Ver todas las noticias →" de `views/dashboard.py` guarda el ticker
elegido en `st.session_state["noticias_ticker"]` antes de navegar aquí —
si esa clave existe, se usa para preseleccionar el ticker (en vez de caer
al ticker por defecto) y se descarta con `.pop()` para no dejarla
pegada en visitas futuras directas desde la navegación.
"""

from __future__ import annotations

import sys
from pathlib import Path

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_access as da  # noqa: E402

st.title("Noticias y sentimiento de una acción")
st.caption(
    "Qué se ha publicado sobre esta acción y con qué tono — informativo, para hacerte una idea del ruido "
    "mediático alrededor de una acción. No es una señal que el modelo use para predecir con fiabilidad "
    "(ver ¿Ayuda el sentimiento a acertar más? en CONTEXTO.md)."
)

engine = da.get_engine()


@st.cache_data(ttl=300)
def _tickers():
    return da.list_tickers(engine)


@st.cache_data(ttl=300)
def _tickers_by_sector():
    return da.get_tickers_by_sector(engine)


@st.cache_data(ttl=300)
def _coverage():
    return da.news_coverage_status(engine)


tickers = _tickers()
if not tickers:
    st.error("No hay tickers con datos en daily_prices. Ejecuta antes download.py → load.py.")
    st.stop()

coverage = _coverage()

meta_tickers = _tickers_by_sector()
sectores = ["Todos"] + sorted(meta_tickers["sector"].dropna().unique().tolist())

_preselect = st.session_state.pop("noticias_ticker", None)

col_s, col_t = st.columns([1, 2])
with col_s:
    sector = st.selectbox("Sector", sectores, index=0)
tickers_filtrados = (
    tickers if sector == "Todos"
    else [t for t in tickers if t in set(meta_tickers.loc[meta_tickers["sector"] == sector, "ticker"])]
) or tickers
if _preselect in tickers_filtrados:
    _default_idx = tickers_filtrados.index(_preselect)
else:
    _default_idx = da.default_ticker_index(tickers_filtrados)
with col_t:
    ticker = st.selectbox("Ticker", tickers_filtrados, index=_default_idx)
meta = da.get_ticker_metadata(engine, ticker)
st.caption(
    f"**{meta.get('nombre') or ticker}** · {meta.get('sector') or 'sector desconocido'} · "
    f"{meta.get('pais') or 'país desconocido'}"
)

st.divider()

daily = da.get_daily_sentiment_for_ticker(engine, ticker, months=6)

if daily.empty:
    st.info(
        f"Todavía no hay noticias guardadas de {ticker} — el backfill de noticias sigue en marcha "
        f"({coverage['tickers_cubiertos']}/{coverage['tickers_totales']} tickers cubiertos por ahora). "
        "Vuelve en unos días.",
        icon="🕒",
    )
    st.stop()

n_articulos = int(daily["n_articles"].sum())
sentimiento_medio = (daily["avg_sentiment_score"] * daily["n_articles"]).sum() / n_articulos

with st.container(border=True):
    col1, col2, col3 = st.columns(3)
    col1.metric("Artículos (últimos 6 meses)", n_articulos)
    col2.metric("Tono medio", f"{sentimiento_medio:+.2f}", help="De -1 (muy negativo) a +1 (muy positivo)")
    col3.metric("Días con cobertura", len(daily))

st.divider()

# --- Tendencia: tono medio (línea) + volumen de artículos (barras) ---
st.subheader("Tendencia")
fig = make_subplots(specs=[[{"secondary_y": True}]])
fig.add_trace(
    go.Bar(x=daily["date"], y=daily["n_articles"], name="Nº de artículos", marker_color="#EEF0F2"),
    secondary_y=False,
)
fig.add_trace(
    go.Scatter(
        x=daily["date"], y=daily["avg_sentiment_score"], name="Tono medio", mode="lines+markers",
        line=dict(color="#1D4ED8", width=2), marker=dict(size=5),
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

st.divider()

# --- Titulares recientes ---
st.subheader("Titulares recientes")
st.caption(
    "Alpha Vantage etiqueta un artículo con esta acción en cuanto la MENCIONA, aunque sea de pasada — "
    "muchos titulares no son en realidad sobre esta empresa (ej. \"F5 lanza una suite con NVIDIA\" aparece "
    "bajo NVDA con relevancia baja). Por eso se filtra por relevancia mínima: solo se muestran artículos "
    "que hablan de verdad de esta acción."
)
min_relevancia = st.slider(
    "Relevancia mínima", min_value=0.3, max_value=1.0, value=0.7, step=0.05,
    help="Más alto = solo artículos centrados de verdad en esta acción (menos titulares, pero más precisos).",
)
articulos = da.get_recent_articles(engine, ticker, limit=25, min_relevance=min_relevancia)

if articulos.empty:
    st.info(
        "Ningún artículo reciente supera ese umbral de relevancia para esta acción — prueba a bajarlo.",
        icon="🔎",
    )

_color_by_label = {
    "Bullish": "#2ca02c", "Somewhat-Bullish": "#86c98a",
    "Neutral": "#9CA3AF",
    "Somewhat-Bearish": "#e79a97", "Bearish": "#d62728",
}
for _, row in articulos.iterrows():
    label = row["ticker_sentiment_label"]
    color = _color_by_label.get(label, "#9CA3AF")
    label_es = da.SENTIMENT_LABEL_ES.get(label, label)
    with st.container(border=True):
        col_a, col_b = st.columns([5, 1])
        col_a.markdown(f"**{row['title']}**")
        col_a.caption(f"{row['source'] or 'fuente desconocida'} · {row['date']} · relevancia {row['relevance_score']:.0%}")
        col_b.markdown(
            f"<span style='color:{color}; font-weight:500;'>{label_es}</span>",
            unsafe_allow_html=True,
        )

