"""
dashboard/views/rendimiento_del_modelo.py — modelo vs. baselines
obligatorios, sobre el ÚNICO test temporal real (nunca visto en
entrenamiento). Ver CONTEXTO.md, "Contrato de evaluación" y "Honestidad de
resultado": si el modelo no supera a los baselines, se muestra igual. Vive
en views/, no pages/ — ver nota en views/inicio.py sobre por qué.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_access as da  # noqa: E402

st.title("¿Funciona de verdad el modelo?")
st.markdown(
    "Para saberlo, se prueba el modelo contra datos que **nunca ha visto** — como un examen con "
    "preguntas que el alumno no ha estudiado — y se compara con dos formas muy simples de adivinar. "
    "Si no les gana, el modelo no está aportando nada real."
)


@st.cache_resource
def _model_bundle():
    return da.load_latest_model()


try:
    bundle = _model_bundle()
except FileNotFoundError as exc:
    st.error(f"No hay ningún modelo entrenado todavía: {exc}. Ejecuta antes `python src/model.py`.")
    st.stop()

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

rows = [
    {"Método": f"Modelo ({bundle.get('model_type')})", **m_model},
    {"Método": "Predecir siempre lo más frecuente", **m_majority},
    {"Método": "Predecir que mañana repite a hoy", **m_persistence},
]
df = pd.DataFrame(rows).rename(columns={"accuracy": "Accuracy", "precision": "Precision", "recall": "Recall"})
for col in ["Accuracy", "Precision", "Recall"]:
    df[col] = (df[col] * 100).round(1).astype(str) + " %"
with st.container(border=True):
    st.dataframe(df, width="stretch", hide_index=True)

    fig = go.Figure()
    _colors = {"Accuracy": "#1D4ED8", "Precision": "#0B0F19", "Recall": "#9CA3AF"}
    for metric, key in [("Accuracy", "accuracy"), ("Precision", "precision"), ("Recall", "recall")]:
        fig.add_trace(
            go.Bar(name=metric, x=[r["Método"] for r in rows], y=[r[key] for r in rows], marker_color=_colors[metric])
        )
    fig.update_layout(
        barmode="group", height=420, margin=dict(l=10, r=10, t=20, b=10),
        yaxis_title="Aciertos (de 0 a 1)",
        plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
    )
    st.plotly_chart(fig, width="stretch")

st.divider()

st.subheader('Por qué comparar contra estas dos formas "tontas" de predecir')
st.markdown(
    """
- **Predecir siempre lo más frecuente** (calculado solo con los datos más antiguos, nunca con los que se
  usan para comprobar el modelo): mira qué pasó más veces en el pasado — sube o baja — y apuesta siempre
  por eso. Si el modelo no le gana, es como si no hiciera nada.
- **Predecir que mañana repite a hoy**: apuesta a que la tendencia de hoy sigue mañana. Parece una
  apuesta simplona, pero en bolsa no es nada fácil de superar.

Ver `CONTEXTO.md`, sección "Contrato de evaluación", para el porqué de este diseño.
    """
)
