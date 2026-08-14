"""
dashboard/views/seguimiento_real.py — acierto REAL en producción, no
backtest: cruza `predictions.predicted_target_up_down` con
`predictions.actual_target_up_down` una vez que `src/track_predictions.py`
ya conoce el cierre real (ver CONTEXTO.md, "Seguimiento real de
predicciones"). Vive en views/, no pages/ — ver nota en views/inicio.py
sobre por qué.

Regla de oro de esta página, aprendida el 2026-08-13: NUNCA agregar el
acierto mezclando `model_version` distintos — un modelo obsoleto sobre un
día de mercado atípico puede hundir el número agregado sin que haya
ningún problema real, y parece un fallo grave cuando no lo es. Por eso
todo aquí se filtra por una única versión a la vez, nunca se promedia
entre versiones.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_access as da  # noqa: E402

st.title("¿Sigue funcionando en el día a día?")
st.markdown(
    "La página **¿Funciona de verdad?** compara el modelo contra un examen histórico (backtest). "
    "Esta es distinta: mira las predicciones **reales** que el sistema fue haciendo día a día, y las "
    "compara con lo que **de verdad pasó** después — el mejor termómetro de si sigue funcionando en "
    "producción, no solo en el laboratorio."
)

engine = da.get_engine()


@st.cache_data(ttl=300)
def _resolved():
    return da.get_resolved_predictions(engine)


df = _resolved()

if df.empty:
    st.info(
        "Todavía no hay ninguna predicción resuelta — hace falta que pase al menos un día de mercado "
        "desde que se predijo, y que `track_predictions.py` se haya ejecutado (ver "
        "`run_daily_pipeline.bat`). Vuelve en unos días.",
        icon="🕒",
    )
    st.stop()

st.divider()

# --- Comparativa por versión de modelo, SIN mezclar ---
st.subheader("Por versión de modelo")
st.caption(
    "Cada fila es una versión de modelo distinta, entrenada en un momento distinto — nunca se mezclan "
    "entre sí en el mismo cálculo de acierto (ver aviso más abajo sobre por qué)."
)

resumen = (
    df.groupby("model_version")
    .agg(
        primera_fecha=("date_predicha", "min"),
        ultima_fecha=("date_predicha", "max"),
        dias_de_mercado=("date_predicha", "nunique"),
        n_predicciones=("acierto", "size"),
        acierto=("acierto", "mean"),
    )
    .sort_values("ultima_fecha")
    .reset_index()
)
resumen_view = resumen.rename(columns={
    "model_version": "Modelo",
    "primera_fecha": "Desde",
    "ultima_fecha": "Hasta",
    "dias_de_mercado": "Sesiones de mercado",
    "n_predicciones": "Predicciones resueltas",
    "acierto": "Acierto real",
})
resumen_view["Desde"] = resumen_view["Desde"].dt.date.astype(str)
resumen_view["Hasta"] = resumen_view["Hasta"].dt.date.astype(str)
resumen_view["Acierto real"] = (resumen_view["Acierto real"] * 100).round(1).astype(str) + " %"
with st.container(border=True):
    st.dataframe(resumen_view, width="stretch", hide_index=True)

st.caption(
    "⚠️ Cada \"sesión de mercado\" son ~208 predicciones (una por ticker), pero no son 208 datos "
    "independientes — son 208 acciones reaccionando al mismo día de mercado. La columna que de verdad "
    "mide el tamaño de la muestra es \"Sesiones de mercado\", no \"Predicciones resueltas\". Con pocas "
    "sesiones, el acierto real puede moverse mucho de una versión a otra sin que signifique nada todavía "
    "— ver CONTEXTO.md, \"Seguimiento real de predicciones\"."
)

st.divider()

# --- Evolución día a día de la versión seleccionada ---
st.subheader("Evolución día a día")

versiones = resumen.sort_values("ultima_fecha", ascending=False)["model_version"].tolist()
version_elegida = st.selectbox("Versión de modelo", versiones, index=0)

sub = df[df["model_version"] == version_elegida].copy()
por_dia = (
    sub.groupby("date_predicha")
    .agg(n=("acierto", "size"), acierto=("acierto", "mean"), sube_predicho=("predicted_target_up_down", "mean"), sube_real=("actual_target_up_down", "mean"))
    .reset_index()
)

fig = go.Figure()
fig.add_trace(
    go.Bar(
        x=por_dia["date_predicha"], y=por_dia["acierto"], name="Acierto real",
        marker_color="#1D4ED8",
    )
)
fig.add_hline(y=0.5, line_dash="dot", line_color="#9CA3AF", annotation_text="mitad = como tirar una moneda")
fig.update_layout(
    height=380, margin=dict(l=10, r=10, t=20, b=10),
    yaxis=dict(title="Acierto", range=[0, 1], tickformat=".0%"),
    xaxis_title=None,
    plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
)
with st.container(border=True):
    st.plotly_chart(fig, width="stretch")

with st.expander("Ver el detalle día a día (predicho vs. real)"):
    detalle = por_dia.rename(columns={
        "date_predicha": "Fecha", "n": "Predicciones", "acierto": "Acierto",
        "sube_predicho": "% predijo \"sube\"", "sube_real": "% subió de verdad",
    }).copy()
    detalle["Fecha"] = detalle["Fecha"].dt.date.astype(str)
    for c in ["Acierto", "% predijo \"sube\"", "% subió de verdad"]:
        detalle[c] = (detalle[c] * 100).round(1).astype(str) + " %"
    st.dataframe(detalle, width="stretch", hide_index=True)

st.caption(
    "Si el % que predijo \"sube\" y el % que subió de verdad se separan mucho un día concreto, suele ser "
    "una sesión de mercado atípica (una caída o subida generalizada) más que un fallo del modelo — "
    "compáralo con varias sesiones antes de sacar conclusiones."
)
