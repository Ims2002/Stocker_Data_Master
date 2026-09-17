"""
dashboard/views/rendimiento_del_modelo.py — modelo vs. baselines
obligatorios, sobre el ÚNICO test temporal real (nunca visto en
entrenamiento). Ver CONTEXTO.md, "Contrato de evaluación" y "Honestidad de
resultado": si el modelo no supera a los baselines, se muestra igual. Vive
en views/, no pages/ — ver nota en views/inicio.py sobre por qué.

REINCORPORADA A LA NAVEGACIÓN 2026-09-09 (se había quitado el
2026-09-08, ver CONTEXTO.md "Quitar '¿Funciona de verdad?' y profundizar
en noticias desde el Dashboard"). Vuelve por un motivo distinto al que la
quitó: feedback de un tutor sobre `predicted_probability` ("no la
vendería como confianza sin comprobar calibración", ver CONTEXTO.md
"Calibración de predicted_probability") — esta página es el sitio
natural para esa comprobación, junto a las métricas de acierto que ya
mostraba. Se añade además un selector de horizonte (día/semana/mes, no
existía antes — la página solo cargaba el horizonte por defecto) para
poder comprobar la calibración de los tres modelos, no solo el diario.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_access as da  # noqa: E402
import ui  # noqa: E402

engine = da.get_engine()

HORIZON_LABELS = {1: "Día (mañana)", 5: "Semana (~5 sesiones)", 20: "Mes (~20 sesiones)"}
_LABEL_TO_HORIZON = {v: k for k, v in HORIZON_LABELS.items()}

st.title("¿Funciona de verdad el modelo?")
st.markdown(
    "Para saberlo, se prueba el modelo contra datos que **nunca ha visto** — como un examen con "
    "preguntas que el alumno no ha estudiado — y se compara con dos formas muy simples de adivinar. "
    "Si no les gana, el modelo no está aportando nada real."
)

horizonte_label = st.segmented_control(
    "Horizonte", list(HORIZON_LABELS.values()), default=HORIZON_LABELS[1], key="rendimiento_horizonte",
)
horizon = _LABEL_TO_HORIZON[horizonte_label] if horizonte_label else 1


@st.cache_resource
def _model_bundle(horizon: int, model_version: str):
    # model_version solo forma parte de la clave de caché (auditoría, M4).
    return da.load_latest_model(horizon=horizon)


_version = da.latest_model_version(horizon)
if _version is None:
    st.info(
        f"Todavía no hay ningún modelo entrenado para el horizonte **{horizonte_label}** — ejecuta "
        f"`python src/model.py --horizon {horizon}`. Elige **Día (mañana)** mientras tanto.",
        icon="🚧",
    )
    st.stop()
bundle = _model_bundle(horizon, _version)

if "metrics_model" not in bundle:
    st.warning(
        "El modelo cargado se guardó antes de que empezáramos a guardar estos resultados junto con él. "
        "Ejecuta de nuevo `python src/model.py` para poder ver esta página con datos reales.",
        icon="⚠️",
    )
    st.stop()

st.caption(
    f"Modelo `{bundle.get('_model_version', '?')}` ({bundle.get('model_type')}), entrenado "
    f"{bundle.get('trained_at', 'fecha desconocida')}. Se entrenó con datos hasta "
    f"{bundle.get('train_date_max')}, y se examinó con datos posteriores, del "
    f"{bundle.get('test_date_min')} al {bundle.get('test_date_max')} "
    f"({bundle.get('train_rows'):,} filas para aprender / {bundle.get('test_rows'):,} filas para el examen)."
)

m_model = bundle["metrics_model"]
m_majority = bundle["metrics_majority_baseline"]
m_persistence = bundle["metrics_persistence_baseline"]

beats_majority = m_model["accuracy"] > m_majority["accuracy"]
beats_persistence = m_model["accuracy"] > m_persistence["accuracy"]

if beats_majority and beats_persistence:
    st.success("Buenas noticias: el modelo le gana a las dos formas simples de adivinar.", icon="✅")
else:
    st.warning(
        "El modelo NO le gana de forma clara a esas dos formas simples de adivinar. Esto es un resultado "
        "honesto que se muestra tal cual, no se esconde — a corto plazo el precio de las acciones se "
        "comporta casi como si fuera aleatorio, así que es un resultado esperable, no un fallo del "
        "proyecto (más detalle en CONTEXTO.md).",
        icon="⚠️",
    )

st.divider()

st.subheader("Los números, en tres preguntas sencillas")
st.markdown(
    """
- **De cada 100 predicciones, ¿cuántas acertó?** → esto es el *Accuracy*.
- **Cuando dijo "va a subir", ¿cuántas veces acertó?** → esto es la *Precision*.
- **De todas las veces que la acción realmente subió, ¿cuántas las detectó?** → esto es el *Recall*.

Cuanto más cerca de 100%, mejor — pero un número alto por sí solo no dice nada si no se compara contra
algo. Por eso lo comparamos con dos formas "tontas" de predecir (los baselines de abajo): si el modelo no
les gana, no está aportando nada de verdad.
    """
)

_persistencia_label = {
    1: "Predecir que mañana repite a hoy",
    5: "Predecir que la semana repite a la anterior",
    20: "Predecir que el mes repite al anterior",
}.get(horizon, "Persistencia")
_base_cols = ("accuracy", "precision", "recall")
rows = [
    {"Método": f"Modelo ({bundle.get('model_type')})", **{k: m_model[k] for k in _base_cols}},
    {"Método": "Predecir siempre lo más frecuente", **{k: m_majority[k] for k in _base_cols}},
    {"Método": _persistencia_label, **{k: m_persistence[k] for k in _base_cols}},
]
df = pd.DataFrame(rows).rename(columns={"accuracy": "Accuracy", "precision": "Precision", "recall": "Recall"})
for col in ["Accuracy", "Precision", "Recall"]:
    df[col] = (df[col] * 100).round(1).astype(str) + " %"
with ui.card("metricas"):
    st.dataframe(df, width="stretch", hide_index=True)

    fig = go.Figure()
    _p = ui.pal()
    _colors = {"Accuracy": _p["accent"], "Precision": _p["second"], "Recall": _p["neutral"]}
    for metric, key in [("Accuracy", "accuracy"), ("Precision", "precision"), ("Recall", "recall")]:
        fig.add_trace(
            go.Bar(name=metric, x=[r["Método"] for r in rows], y=[r[key] for r in rows], marker_color=_colors[metric])
        )
    ui.style_fig(fig, height=420, barmode="group", yaxis_title="Aciertos (de 0 a 1)")
    ui.plotly_chart(fig)

# Métricas de probabilidad (auditoría, M1): solo en modelos entrenados tras
# la revisión. La accuracy con umbral 0,5 no dice si el modelo ordena bien
# los casos; el AUC sí (0,5 = azar).
if "roc_auc" in m_model:
    c1, c2 = st.columns(2)
    c1.metric("AUC", f"{m_model['roc_auc']:.3f}", help="0,5 = como tirar una moneda; 1 = ordena perfectamente.")
    c2.metric("Log loss", f"{m_model['log_loss']:.4f}", help="Menor es mejor. 0,693 = decir siempre 50 %.")
if horizon != 1 and "roc_auc" not in m_model:
    st.caption(
        "ℹ️ Este modelo se entrenó antes de la revisión de auditoría: su baseline de persistencia usa el "
        "movimiento de un solo día también para este horizonte. Reentrena para ver la comparación justa."
    )

st.divider()

st.subheader('Por qué comparar contra estas dos formas "tontas" de predecir')
st.markdown(
    """
- **Predecir siempre lo más frecuente** (calculado solo con los datos más antiguos, nunca con los que se
  usan para comprobar el modelo): mira qué pasó más veces en el pasado — sube o baja — y apuesta siempre
  por eso. Si el modelo no le gana, es como si no hiciera nada.
- **Persistencia**: apuesta a que la tendencia reciente continúa — la de hoy para el horizonte día, la
  de la última semana para el semanal y la del último mes para el mensual. Parece una apuesta simplona,
  pero en bolsa no es nada fácil de superar.

Ver `CONTEXTO.md`, sección "Contrato de evaluación", para el porqué de este diseño.
    """
)

st.divider()

st.subheader("¿Es fiable la probabilidad que muestra el modelo?")
st.markdown(
    """
El modelo no solo dice "sube" o "baja": en "Predicciones" y "Dashboard" también enseña una probabilidad
para esa dirección. Para que esa probabilidad se pueda leer como una confianza real, tiene que estar
**calibrada**: si el modelo dice "70% de confianza" en un grupo de predicciones, ese grupo debería acertar
aproximadamente el 70% de las veces — ni más ni menos. Random Forest no lo garantiza por diseño, así que
se comprueba con datos reales del test oficial en vez de asumirlo (feedback recibido de un tutor del TFM,
ver CONTEXTO.md "Calibración de predicted_probability").
    """
)


@st.cache_data(ttl=300)
def _calibration(_model_version: str):
    # _model_version solo participa en la clave de caché (mismo patrón
    # que _confidence_accuracy en views/inicio.py) — la función usa el
    # bundle vía closure.
    return da.get_calibration_diagnostic(engine, bundle)


calib = _calibration(bundle.get("_model_version", ""))
if calib is None:
    st.caption("Sin datos suficientes en el test real todavía para calcular la calibración.")
else:
    col_a, col_b = st.columns([1, 2])
    with col_a:
        st.metric(
            "Brier score", f"{calib['brier_score']:.3f}",
            help="0 = perfecto. 0.25 = tan informativo como predecir siempre 50%. Cuanto más bajo, mejor.",
        )
        st.caption(
            f"Calculado sobre las {sum(b['n'] for b in calib['reliability_table']):,} predicciones "
            "resueltas del test oficial, en vivo (no un valor congelado del momento del entrenamiento)."
        )
    with col_b:
        tabla = calib["reliability_table"]
        if not tabla:
            st.caption("No hay suficientes cubos distintos de confianza para dibujar la curva.")
        else:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=[0.5, 1.0], y=[0.5, 1.0], mode="lines", name="Calibración perfecta",
                line=dict(color=ui.pal()["muted"], dash="dot"),
            ))
            fig.add_trace(go.Scatter(
                x=[b["confianza_media"] for b in tabla], y=[b["acierto_real"] for b in tabla],
                mode="lines+markers", name="Modelo (real)",
                line=dict(color=ui.pal()["accent"], width=2), marker=dict(size=7),
                text=[f"n={b['n']}" for b in tabla], hovertemplate="confianza %{x:.0%} · acierto %{y:.0%} · %{text}<extra></extra>",
            ))
            ui.style_fig(fig, height=320, xaxis_title="Confianza declarada", yaxis_title="Acierto real observado")
            fig.update_xaxes(range=[0.45, 1.02], tickformat=".0%")
            fig.update_yaxes(range=[0.0, 1.02], tickformat=".0%")
            with ui.card("calibracion"):
                ui.plotly_chart(fig)
    st.caption(
        "Cada punto agrupa un décimo de las predicciones del test por confianza declarada. Cuanto más se "
        "acerque la línea de color a la diagonal punteada, más calibrada está la probabilidad. Por debajo de la "
        "diagonal: el modelo dice tener más confianza de la que realmente demuestra — es el caso a "
        "vigilar antes de presentar esta probabilidad como \"confianza\" sin más. Por encima: el modelo es "
        "más certero de lo que dice (infravalora su propia confianza, menos grave para el usuario)."
    )
