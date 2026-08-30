"""
dashboard/views/dashboard.py — "Dashboard": página de entrada del v1
(2026-08-27, ver CONTEXTO.md "Roadmap v1 (MVP para publicar)"). Nació
como boceto separado de "Resumen" (views/inicio.py, 2026-08-25), pero al
preparar el v1 se decidió que sustituye a "Resumen" como entrada — dos
páginas contando una historia parecida no aportaba para una primera
publicación. "Resumen" sigue existiendo como archivo (no se borra), solo
dejó de registrarse en Inicio.py.

Diseño acordado con el usuario antes de escribir código (2026-08-25):
- Barra lateral con filtros (sector, ticker, horizonte, meses de
  histórico) — texto e indicadores dinámicos según la selección.
- Al elegir un ticker, TODO el dashboard se recentra a esa acción (KPIs de
  cabecera, gráfico, noticias, texto de insights) — no solo la tabla de
  noticias. Con st.columns, que ya apila verticalmente en pantallas
  estrechas (comportamiento nativo de Streamlit, sin CSS a mano), para que
  se lea bien en móvil.
- Un horizonte a la vez con selector (día/semana/mes), igual que
  "Predicciones", no los tres en paralelo.

RECORTADO 2026-08-25 (feedback del usuario tras ver el primer boceto): se
quitó el medidor de sentimiento general del mercado (era la única pieza
que NO se recentraba en la acción elegida, rompía la idea de "todo gira
en torno a una acción") y la tarjeta/enlace de "¿Funciona de verdad?"
(quedaba redundante con esa página dedicada, ya accesible desde la
navegación superior). El gráfico sigue marcando aciertos/fallos del
backtest sobre el precio — solo se quitó el KPI agregado y el texto que
lo repetían.

RECORTADO OTRA VEZ 2026-08-27 (alcance v1): el enlace de pie de página a
"Noticias de la acción" se quitó porque esa página ya no está registrada
en la navegación del v1 (ver Inicio.py) — enlazar a una página no
registrada rompe `st.page_link`. Solo queda el enlace a "Predicciones".

CABECERA REHECHA 2026-08-28: se quitó el `st.title("Dashboard")` (era
redundante con la pestaña de navegación, que ya dice "Dashboard"). Se
probó primero con un `st.title("STOCKER")` encima del subtítulo, pero
también se quitó el mismo día (redundante con el wordmark "Stocker" que
ya está en la barra de navegación superior — `ui.render_logo()` — no
hacía falta repetirlo dentro del contenido de la página). La cabecera
final es solo el subtítulo dinámico: logo del ticker + nombre de la
empresa + `#TICKER`, en gris claro `#9CA3AF` (mismo tono que ya se usaba
para "Neutral" en el sentimiento). El subtítulo depende de la acción
elegida en la barra lateral, así que se pinta después de leerla, no
antes. El aviso de fecha de datos (demo congelada) sigue justo debajo.

Detalle completo de todas estas decisiones en CONTEXTO.md, "Dashboard
unificado (mockup)" y "Roadmap v1 (MVP para publicar)".

Vive en views/, no pages/ — ver la nota en views/inicio.py sobre por qué.
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_access as da  # noqa: E402

engine = da.get_engine()

HORIZON_LABELS = {1: "Día (mañana)", 5: "Semana (~5 sesiones)", 20: "Mes (~20 sesiones)"}
_LABEL_TO_HORIZON = {v: k for k, v in HORIZON_LABELS.items()}

_SENTIMENT_COLOR = {
    "Bearish": "#d62728", "Somewhat-Bearish": "#e79a97",
    "Neutral": "#9CA3AF",
    "Somewhat-Bullish": "#86c98a", "Bullish": "#2ca02c",
}


@st.cache_data(ttl=300)
def _tickers():
    return da.list_tickers(engine)


@st.cache_data(ttl=300)
def _tickers_by_sector():
    return da.get_tickers_by_sector(engine)


@st.cache_resource
def _model_bundle(horizon: int):
    return da.load_latest_model(horizon=horizon)


@st.cache_data(ttl=300)
def _price_history(ticker: str, months: int):
    return da.get_price_history(engine, ticker, months=months)


@st.cache_data(ttl=300)
def _gold_for_ticker(ticker: str, months: int):
    return da.get_gold_train_for_ticker(engine, ticker, months=months)


@st.cache_data(ttl=300)
def _recent_articles(ticker: str):
    return da.get_recent_articles(engine, ticker, limit=6, min_relevance=0.5)


@st.cache_data(ttl=300)
def _daily_sentiment(ticker: str):
    return da.get_daily_sentiment_for_ticker(engine, ticker, months=1)


tickers = _tickers()
if not tickers:
    st.error("No hay tickers con datos en daily_prices. Ejecuta antes download.py → load.py.")
    st.stop()

meta_tickers = _tickers_by_sector()
sectores = ["Todos"] + sorted(meta_tickers["sector"].dropna().unique().tolist())

with st.sidebar:
    st.subheader("Filtros")
    sector = st.selectbox("Sector", sectores, index=0)
    tickers_filtrados = (
        tickers if sector == "Todos"
        else [t for t in tickers if t in set(meta_tickers.loc[meta_tickers["sector"] == sector, "ticker"])]
    ) or tickers
    ticker = st.selectbox("Acción", tickers_filtrados, index=da.default_ticker_index(tickers_filtrados))
    horizonte_label = st.segmented_control(
        "Horizonte de predicción", list(HORIZON_LABELS.values()), default=HORIZON_LABELS[1],
    )
    meses = st.slider("Meses de histórico", min_value=1, max_value=24, value=6)
    st.caption(
        f"Mostrando **{len(tickers_filtrados)}** acción(es)"
        + (f" del sector **{sector}**" if sector != "Todos" else " de todo el universo")
        + " — todo el dashboard de abajo se recentra a la acción elegida."
    )

horizon = _LABEL_TO_HORIZON[horizonte_label] if horizonte_label else 1
target_col = "target_up_down" if horizon == 1 else f"target_up_down_{horizon}d"

meta = da.get_ticker_metadata(engine, ticker)
logo_url = da.ticker_logo_url(ticker)

_logo_html = (
    f'<img src="{logo_url}" width="26" height="26" '
    'style="border-radius:6px; vertical-align:middle; margin-right:8px;">'
) if logo_url else ""
_nombre_empresa = html.escape(meta.get("nombre") or ticker)
st.markdown(
    f'<div style="margin-top:4px;">{_logo_html}'
    f'<span style="color:#9CA3AF; font-size:1.05rem; vertical-align:middle;">'
    f'{_nombre_empresa} · <strong>#{ticker}</strong></span></div>',
    unsafe_allow_html=True,
)
st.caption(f"{meta.get('sector') or 'sector desconocido'} · {meta.get('pais') or 'país desconocido'}")

try:
    bundle = _model_bundle(horizon)
except FileNotFoundError:
    bundle = None

if bundle is None:
    st.info(
        f"Todavía no hay ningún modelo entrenado para el horizonte **{horizonte_label}** — ejecuta "
        f"`python src/model.py --horizon {horizon}` (y `python src/predict.py --horizon {horizon}`). "
        "Elige **Día (mañana)** mientras tanto.",
        icon="🚧",
    )
    st.stop()

prices = _price_history(ticker, meses)
if prices.empty:
    st.warning(f"No hay histórico de precios para {ticker} en la ventana seleccionada.")
    st.stop()

gold = _gold_for_ticker(ticker, meses)
n_sin_target = 0
if not gold.empty and horizon != 1:
    n_sin_target = int(gold[target_col].isna().sum())
    gold = gold[gold[target_col].notna()].copy()

latest_pred = da.get_latest_prediction(engine, ticker, horizon=horizon)

last_close = float(prices["close"].iloc[-1])
prev_close = float(prices["close"].iloc[-2]) if len(prices) >= 2 else None
var_pct = (last_close / prev_close - 1) if prev_close else None

# --- KPIs de cabecera, recentrados en la acción elegida ---
with st.container(border=True):
    col1, col2, col3 = st.columns(3)
    if latest_pred and latest_pred["predicted_target_up_down"] is not None:
        direccion = "📈 Sube" if latest_pred["predicted_target_up_down"] == 1 else "📉 Baja"
        col1.metric(f"Predicción · {latest_pred['date_predicha']}", direccion)
        col2.metric("Probabilidad estimada", f"{latest_pred['predicted_probability']:.1%}")
    else:
        col1.metric("Predicción", "—")
        col2.metric("Probabilidad estimada", "—")
    col3.metric(
        "Último cierre",
        f"${last_close:,.2f}",
        delta=f"{var_pct:+.2%}" if var_pct is not None else None,
    )

st.divider()

# --- Gráfico de precio con aciertos/fallos del backtest + predicción destacada ---
fig = go.Figure()
fig.add_trace(
    go.Scatter(
        x=prices["date"], y=prices["close"], mode="lines", name="Cierre real",
        line=dict(color="#1D4ED8", width=1.5),
    )
)

test_date_min = bundle.get("test_date_min")
gold_eval = pd.DataFrame()
if not gold.empty:
    gold = gold.copy()
    gold["pred"] = da.predict_for_gold_rows(bundle, gold)
    gold["acierto"] = gold["pred"] == gold[target_col]

    if test_date_min:
        test_cutoff = pd.Timestamp(test_date_min)
        fig.add_vline(
            x=test_cutoff, line_dash="dot", line_color="gray",
            annotation_text="a partir de aquí, examen real", annotation_position="top",
        )
        gold_eval = gold[gold["date"] >= test_cutoff]

    aciertos = gold[gold["acierto"]]
    fallos = gold[~gold["acierto"]]
    fig.add_trace(go.Scatter(
        x=aciertos["date"], y=aciertos["close"], mode="markers", name="Acertó",
        marker=dict(color="#2ca02c", size=6, symbol="circle"),
    ))
    fig.add_trace(go.Scatter(
        x=fallos["date"], y=fallos["close"], mode="markers", name="Falló",
        marker=dict(color="#d62728", size=6, symbol="x"),
    ))

if latest_pred and latest_pred["predicted_target_up_down"] is not None:
    direccion = "SUBE" if latest_pred["predicted_target_up_down"] == 1 else "BAJA"
    color = "#2ca02c" if latest_pred["predicted_target_up_down"] == 1 else "#d62728"
    fig.add_trace(go.Scatter(
        x=[pd.Timestamp(latest_pred["date_predicha"])], y=[last_close], mode="markers+text",
        name=f"Predicción {latest_pred['date_predicha']}",
        marker=dict(color=color, size=14, symbol="star"), text=[direccion], textposition="top center",
    ))

fig.update_layout(
    height=420, margin=dict(l=10, r=10, t=30, b=10),
    yaxis_title="Precio de cierre (USD)", xaxis_title=None,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
)
with st.container(border=True):
    st.plotly_chart(fig, width="stretch")
if n_sin_target:
    st.caption(
        f"ℹ️ Las últimas {n_sin_target} sesiones de la ventana elegida todavía no tienen las {horizon} "
        "sesiones futuras necesarias para saber si acertaron — no cuentan ni como acierto ni como fallo."
    )

st.divider()

# --- Noticias de la acción elegida (una línea por artículo) ---
st.subheader(f"Últimas noticias de {ticker}")
articulos = _recent_articles(ticker)
with st.container(border=True):
    if articulos.empty:
        st.info(
            f"Todavía no hay noticias guardadas de {ticker} (relevancia ≥ 0.5) — el backfill de "
            "noticias puede seguir en marcha.",
            icon="🕒",
        )
    else:
        filas_html = []
        for _, row in articulos.iterrows():
            label = row["ticker_sentiment_label"]
            color = _SENTIMENT_COLOR.get(label, "#9CA3AF")
            label_es = da.SENTIMENT_LABEL_ES.get(label, label)
            titulo = html.escape(str(row["title"]))
            if len(titulo) > 70:
                titulo = titulo[:68] + "…"
            fuente = html.escape(str(row["source"] or "fuente desconocida"))
            filas_html.append(
                f"""<tr style="border-bottom:1px solid #EEF0F2;">
                    <td style="padding:7px 6px; color:#0B0F19; font-size:13px;">{titulo}</td>
                    <td style="padding:7px 6px; color:#9CA3AF; font-size:12px; white-space:nowrap;">{fuente} · {row['date']}</td>
                    <td style="padding:7px 6px; text-align:right; white-space:nowrap;">
                        <span style="background:{color}22; color:{color}; padding:2px 9px; border-radius:999px; font-size:11px; font-weight:500;">{html.escape(label_es)}</span>
                    </td>
                </tr>"""
            )
        st.markdown(
            f'<table style="width:100%; border-collapse:collapse;"><tbody>{"".join(filas_html)}</tbody></table>',
            unsafe_allow_html=True,
        )
        st.caption("Solo artículos con relevancia ≥ 0.5 para esta acción.")

st.divider()

# --- Insights dinámicos: texto que cambia según la acción/horizonte elegidos ---
st.subheader("Lectura rápida")
with st.container(border=True):
    partes = []
    if latest_pred and latest_pred["predicted_target_up_down"] is not None:
        direccion_txt = "**subirá**" if latest_pred["predicted_target_up_down"] == 1 else "**bajará**"
        partes.append(
            f"El modelo predice que **{ticker}** {direccion_txt} en el horizonte **{horizonte_label.lower()}**, "
            f"con una probabilidad estimada del **{latest_pred['predicted_probability']:.0%}**."
        )
    else:
        partes.append(f"Todavía no hay una predicción guardada para **{ticker}** en este horizonte.")

    daily_sent = _daily_sentiment(ticker)
    if not daily_sent.empty:
        n_art = int(daily_sent["n_articles"].sum())
        tono = float((daily_sent["avg_sentiment_score"] * daily_sent["n_articles"]).sum() / n_art) if n_art else 0.0
        tono_label = da.sentiment_scalar_label(tono)
        partes.append(f"Las noticias de {ticker} del último mes tienen un tono **{tono_label.lower()}** ({n_art} artículos).")
    else:
        partes.append(f"Sin noticias recientes de {ticker} en el último mes.")

    st.markdown(" ".join(partes))
    st.caption(
        "Texto generado a partir de los mismos datos de arriba, no una señal nueva — el sentimiento de "
        "noticias es informativo, sin evidencia de que ayude a predecir el precio (ver CONTEXTO.md)."
    )

st.divider()

st.subheader("Profundizar")
st.page_link("views/predicciones.py", label="Predicciones →", icon="📊")
