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

REVISIÓN DE AUDITORÍA 2026-09-16: modelo recargado al reentrenar (M4),
aviso de fecha de datos (A4), periodo de entrenamiento atenuado en el
gráfico (U2) y la predicción indica su propio modelo y cuándo se hizo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_access as da  # noqa: E402
import ui  # noqa: E402

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
def _model_bundle(horizon: int, model_version: str):
    # model_version solo forma parte de la clave de caché (auditoría, M4).
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
    ticker = st.selectbox("Ticker", tickers_filtrados, index=da.default_ticker_index(tickers_filtrados))
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

ui.render_data_freshness(da.get_latest_data_date(engine), da.get_pipeline_status())
logo_url = da.ticker_logo_url(ticker, size=40)
col_logo, col_meta = st.columns([1, 11])
if logo_url:
    with col_logo:
        st.image(logo_url, width=40)
with col_meta:
    st.caption(
        f"**{meta.get('nombre') or ticker}** · {meta.get('sector') or 'sector desconocido'} · "
        f"{meta.get('pais') or 'país desconocido'}"
    )

_version = da.latest_model_version(horizon)
if _version is None:
    st.info(
        f"Todavía no hay ningún modelo entrenado para el horizonte **{horizonte_label}** — ejecuta "
        f"`python src/model.py --horizon {horizon}` (y `python src/predict.py --horizon {horizon}` para "
        "generar predicciones reales con él). Selecciona **Día (mañana)** para ver la predicción real de "
        "hoy mientras tanto.",
        icon="🚧",
    )
    st.stop()
bundle = _model_bundle(horizon, _version)

# Trazabilidad del modelo activo para este horizonte (2026-09-10,
# feedback de un tutor: "no queda claro... si día, semana y mes utilizan
# modelos realmente evaluados para cada horizonte" — ver CONTEXTO.md
# "Feedback de un tutor: comunicación de predicciones poco concluyentes,
# trazabilidad y consistencia del sentimiento"). Los tres horizontes SÍ
# entrenan y evalúan un modelo independiente (ver `model.py --horizon`),
# pero esa evidencia vivía solo en "¿Funciona de verdad?", una pestaña
# distinta sin ningún enlace desde aquí desde el 2026-08-30 — se deja
# visible en el propio sitio donde se muestra la predicción, no solo
# "encontrable" si ya sabes que esa otra pestaña existe.
with ui.card("traza_modelo"):
    st.caption(
        f"Predicción del modelo `{bundle.get('_model_version', '?')}` ({bundle.get('model_type', '?')}), "
        f"entrenado el {bundle.get('trained_at', 'fecha desconocida')[:10] if bundle.get('trained_at') else 'fecha desconocida'} "
        f"y evaluado con un examen real ({bundle.get('test_date_min', '?')} a {bundle.get('test_date_max', '?')}, "
        f"nunca visto en entrenamiento) — un modelo distinto por horizonte, no el mismo reutilizado."
    )
    st.page_link(
        "views/rendimiento_del_modelo.py",
        label="Ver el rendimiento completo y la calibración de este modelo →", icon="📊",
    )

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

# --- Gráfico de precio con aciertos/fallos del backtest (estilo del tema,
# ver ui.price_chart: área en modo claro, velas en modo terminal) ---
test_date_min = bundle.get("test_date_min")
gold_eval = pd.DataFrame()
gold_train_period = pd.DataFrame()
test_cutoff = None
caveat = None
if not gold.empty:
    gold = gold.copy()
    gold["pred"] = da.predict_for_gold_rows(bundle, gold)
    gold["acierto"] = gold["pred"] == gold[target_col]

    if test_date_min:
        test_cutoff = pd.Timestamp(test_date_min)
        gold_eval = gold[gold["date"] >= test_cutoff]
        # Periodo de entrenamiento atenuado (auditoría, U2).
        gold_train_period = gold[gold["date"] < test_cutoff]
    else:
        gold_eval = gold
        caveat = (
            "Este modelo se guardó antes de que empezáramos a distinguir esto — el % de aciertos de abajo "
            "puede incluir días que el modelo ya \"vio\" al entrenar (más fácil de acertar, no es un "
            "examen justo). Vuelve a ejecutar `python src/model.py` para arreglarlo."
        )

# --- Predicción para el horizonte elegido (de la tabla predictions, no recalculada aquí) ---
latest_pred = da.get_latest_prediction(engine, ticker, horizon=horizon)
if latest_pred and latest_pred["model_version"] != bundle.get("_model_version"):
    st.caption(
        f"ℹ️ La predicción guardada se hizo con `{latest_pred['model_version']}`; el gráfico de aciertos usa "
        f"`{bundle.get('_model_version')}`, más reciente. Se igualarán en la próxima ejecución del pipeline."
    )

fig = ui.price_chart(
    prices, gold_eval=gold_eval, gold_train_period=gold_train_period,
    test_cutoff=test_cutoff, prediction=latest_pred, height=480,
)
with ui.card("grafico_predicciones"):
    ui.plotly_chart(fig)

st.divider()

# --- KPIs de la predicción para mañana + backtest de la ventana ---
with ui.card("kpis_predicciones"):
    col1, col2, col3 = st.columns(3)
    if latest_pred and latest_pred["predicted_target_up_down"] is not None:
        direccion = "▲ Sube" if latest_pred["predicted_target_up_down"] == 1 else "▼ Baja"
        col1.metric(
            f"Predicción para {latest_pred['date_predicha']}", direccion,
            help=f"Hecha el {str(latest_pred['predicted_at'])[:16]} con el modelo `{latest_pred['model_version']}`.",
        )
        # Bug corregido 2026-09-09 (ver CONTEXTO.md "Probabilidad
        # mostrada para la dirección equivocada"): `predicted_probability`
        # guarda SIEMPRE P(sube), no la probabilidad de la dirección
        # predicha — con "Baja" mostraba p.ej. "35%" que un usuario lee
        # como "35% de bajar" cuando en realidad son 65%.
        # `da.prediction_confidence()` (2026-09-10) consolida esta cuenta,
        # antes duplicada en cada página. (El aviso de "poco concluyente"
        # que hubo en el KPI de dirección se revirtió a petición del
        # usuario el mismo día — ver CONTEXTO.md.)
        confianza = da.prediction_confidence(
            latest_pred["predicted_target_up_down"], latest_pred["predicted_probability"]
        )
        col2.metric(
            "Probabilidad estimada", f"{confianza:.1%}",
            help=(
                "Probabilidad que el modelo asigna a ESTA dirección concreta. Es la salida cruda de "
                "Random Forest — no está garantizado que esté calibrada como una probabilidad real de "
                "acierto (ver la calibración comprobada en '¿Funciona de verdad?')."
            ),
        )
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
                "histórico, o consulta la página '¿Funciona de verdad?' para el dato oficial."
            )
    else:
        col3.metric("Aciertos en la ventana", "sin datos")

st.caption(
    "Los aciertos/fallos de este gráfico son solo de esta acción, para hacerte una idea visual — el "
    "número oficial (calculado con todas las acciones a la vez, de forma más rigurosa) está en la página "
    "'¿Funciona de verdad?'."
)

st.divider()
# Mismo pie de trazabilidad que Dashboard (2026-09-10, feedback de un
# tutor — ver CONTEXTO.md "Feedback de un tutor: comunicación de
# predicciones poco concluyentes, trazabilidad y consistencia del
# sentimiento").
st.caption(
    "Todas las cifras de esta página salen de `stocker.db`, generado por el pipeline real "
    "(`download.py → load.py → gold.py → model.py → predict.py`, ver `docs/entregas/` para el detalle "
    "metodológico) — el dashboard nunca escribe ni inventa datos."
)
