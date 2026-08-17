"""
dashboard/views/predicciones.py — comparativa histórico vs. predicho por
ticker, con selector de horizonte (día/semana/mes, ver CONTEXTO.md,
"Horizontes de predicción: semana y mes", 2026-08-13). Cada horizonte
necesita su propio modelo entrenado (`model.py --horizon 5/20`) — si
todavía no existe, esta página lo avisa y no se inventa nada, en vez de
mostrar un backtest vacío o incorrecto (ver "Honestidad de resultado" en
CONTEXTO.md). set_page_config y el tema visual viven en Inicio.py
(entrypoint del enrutador), no aquí. Vive en views/, no pages/ — ver nota
en views/inicio.py sobre por qué.
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

# Etiquetas del selector de horizonte — las claves son sesiones de mercado
# (ver config.PREDICTION_HORIZONS), no días naturales.
HORIZON_LABELS = {1: "Día (mañana)", 5: "Semana (~5 sesiones)", 20: "Mes (~20 sesiones)"}
_LABEL_TO_HORIZON = {v: k for k, v in HORIZON_LABELS.items()}


@st.cache_data(ttl=300)
def _tickers():
    return da.list_tickers(engine)


@st.cache_data(ttl=300)
def _tickers_by_sector():
    return da.get_tickers_by_sector(engine)


@st.cache_resource
def _model_bundle(horizon: int):
    return da.load_latest_model(horizon=horizon)


tickers = _tickers()
if not tickers:
    st.error("No hay tickers con datos en daily_prices. Ejecuta antes download.py → load.py.")
    st.stop()

meta_tickers = _tickers_by_sector()
sectores = ["Todos"] + sorted(meta_tickers["sector"].dropna().unique().tolist())

col_s, col_a, col_b, col_c = st.columns([1.2, 1.6, 2, 1])
with col_s:
    sector = st.selectbox("Sector", sectores, index=0)
tickers_filtrados = (
    tickers if sector == "Todos"
    else [t for t in tickers if t in set(meta_tickers.loc[meta_tickers["sector"] == sector, "ticker"])]
) or tickers
with col_a:
    ticker = st.selectbox("Ticker", tickers_filtrados, index=0)
with col_b:
    horizonte_label = st.segmented_control(
        "Horizonte de predicción", list(HORIZON_LABELS.values()), default=HORIZON_LABELS[1],
    )
with col_c:
    meses = st.slider("Meses de histórico a mostrar", min_value=1, max_value=24, value=12)

st.divider()

horizon = _LABEL_TO_HORIZON[horizonte_label]
target_col = "target_up_down" if horizon == 1 else f"target_up_down_{horizon}d"

meta = da.get_ticker_metadata(engine, ticker)
st.caption(
    f"**{meta.get('nombre') or ticker}** · {meta.get('sector') or 'sector desconocido'} · "
    f"{meta.get('pais') or 'país desconocido'}"
)

try:
    bundle = _model_bundle(horizon)
except FileNotFoundError:
    st.info(
        f"Todavía no hay ningún modelo entrenado para el horizonte **{horizonte_label}** — ejecuta "
        f"`python src/model.py --horizon {horizon}` (y `python src/predict.py --horizon {horizon}` para "
        "generar predicciones reales con él). Selecciona **Día (mañana)** para ver la predicción real de "
        "hoy mientras tanto.",
        icon="🚧",
    )
    st.stop()

prices = da.get_price_history(engine, ticker, months=meses)
gold = da.get_gold_train_for_ticker(engine, ticker, months=meses)

if prices.empty:
    st.warning(f"No hay histórico de precios para {ticker} en la ventana seleccionada.")
    st.stop()

if not gold.empty and horizon != 1:
    n_sin_target = int(gold[target_col].isna().sum())
    gold = gold[gold[target_col].notna()].copy()
    if n_sin_target:
        st.caption(
            f"ℹ️ Las últimas {n_sin_target} sesiones de la ventana elegida no tienen todavía las "
            f"{horizon} sesiones futuras necesarias para saber si acertaron o no a este horizonte — no "
            "se cuentan como acierto ni como fallo, simplemente no aparecen."
        )

# --- Gráfico de precio con aciertos/fallos del backtest ---
fig = go.Figure()
fig.add_trace(
    go.Scatter(
        x=prices["date"], y=prices["close"], mode="lines", name="Cierre real",
        line=dict(color="#1D4ED8", width=1.5),
    )
)

test_date_min = bundle.get("test_date_min")
if not gold.empty:
    gold = gold.copy()
    gold["pred"] = da.predict_for_gold_rows(bundle, gold)
    gold["acierto"] = gold["pred"] == gold[target_col]

    if test_date_min:
        test_cutoff = pd.Timestamp(test_date_min)
        fig.add_vline(
            x=test_cutoff, line_dash="dot", line_color="gray",
            annotation_text="a partir de aquí, el modelo nunca vio estos datos", annotation_position="top",
        )
        gold_eval = gold[gold["date"] >= test_cutoff]
        caveat = None
    else:
        gold_eval = gold
        caveat = (
            "Este modelo se guardó antes de que empezáramos a distinguir esto — el % de aciertos de abajo "
            "puede incluir días que el modelo ya \"vio\" al entrenar (más fácil de acertar, no es un "
            "examen justo). Vuelve a ejecutar `python src/model.py` para arreglarlo."
        )

    aciertos = gold[gold["acierto"]]
    fallos = gold[~gold["acierto"]]
    fig.add_trace(
        go.Scatter(
            x=aciertos["date"], y=aciertos["close"], mode="markers", name="Acertó",
            marker=dict(color="#2ca02c", size=6, symbol="circle"),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=fallos["date"], y=fallos["close"], mode="markers", name="Falló",
            marker=dict(color="#d62728", size=6, symbol="x"),
        )
    )

# --- Predicción para el horizonte elegido (de la tabla predictions, no recalculada aquí) ---
latest_pred = da.get_latest_prediction(engine, ticker, horizon=horizon)
if latest_pred and latest_pred["predicted_target_up_down"] is not None:
    direccion = "SUBE" if latest_pred["predicted_target_up_down"] == 1 else "BAJA"
    color = "#2ca02c" if latest_pred["predicted_target_up_down"] == 1 else "#d62728"
    last_close = prices["close"].iloc[-1]
    fig.add_trace(
        go.Scatter(
            x=[pd.Timestamp(latest_pred["date_predicha"])], y=[last_close], mode="markers+text",
            name=f"Predicción {latest_pred['date_predicha']}",
            marker=dict(color=color, size=14, symbol="star"),
            text=[direccion], textposition="top center",
        )
    )

fig.update_layout(
    height=500, margin=dict(l=10, r=10, t=30, b=10),
    yaxis_title="Precio de cierre (USD)", xaxis_title=None,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
)
with st.container(border=True):
    st.plotly_chart(fig, width="stretch")

st.divider()

# --- KPIs de la predicción para mañana + backtest de la ventana ---
with st.container(border=True):
    col1, col2, col3 = st.columns(3)
    if latest_pred and latest_pred["predicted_target_up_down"] is not None:
        direccion = "📈 Sube" if latest_pred["predicted_target_up_down"] == 1 else "📉 Baja"
        col1.metric(f"Predicción para {latest_pred['date_predicha']}", direccion)
        col2.metric("Probabilidad estimada", f"{latest_pred['predicted_probability']:.1%}")
    else:
        col1.metric(f"Predicción ({horizonte_label})", "—")
        col2.metric("Probabilidad estimada", "—")

    if not gold.empty:
        col3.metric(
            "Aciertos (examen real)" if test_date_min else "Aciertos (¡puede estar \"copiando\"!)",
            f"{gold_eval['acierto'].mean():.1%}" if not gold_eval.empty else "sin datos suficientes",
        )
        if caveat:
            st.caption(f"⚠️ {caveat}")
        elif gold_eval.empty:
            st.caption(
                "En la ventana elegida no hay días de \"examen real\" — prueba a ampliar los meses de "
                "histórico, o consulta la página 'Rendimiento del modelo' para el dato oficial."
            )
    else:
        col3.metric("Aciertos en la ventana", "sin datos")

st.caption(
    "Los aciertos/fallos de este gráfico son solo de esta acción, para hacerte una idea visual — el "
    "número oficial (calculado con todas las acciones a la vez, de forma más rigurosa) está en la página "
    "'Rendimiento del modelo'."
)
