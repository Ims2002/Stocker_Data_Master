"""
dashboard/views/importancia_de_features.py — qué mira el modelo para
decidir, explicado en lenguaje llano (no solo "claro" para alguien con
formación técnica — ver CONTEXTO.md, feedback del usuario 2026-08-05).
Es UNA importancia global, compartida por todos los tickers — no hay una
por acción, porque el modelo no usa el ticker como feature (ver
model.py, build_feature_matrix). Vive en views/, no pages/ — ver nota en
views/inicio.py sobre por qué.
"""

from __future__ import annotations

import sys
from pathlib import Path

import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_access as da  # noqa: E402

st.title("En qué se fija el modelo para decidir")


@st.cache_resource
def _model_bundle():
    return da.load_latest_model()


try:
    bundle = _model_bundle()
except FileNotFoundError as exc:
    st.error(f"No hay ningún modelo entrenado todavía: {exc}. Ejecuta antes `python src/model.py`.")
    st.stop()

fi = da.feature_importances(bundle)
model_type = bundle.get("model_type", "desconocido")

if fi is None:
    st.warning("Este modelo no permite calcular en qué se fija más o menos.")
    st.stop()

# Nº de features calculado del modelo cargado (bundle["feature_names"] o
# FEATURE_NAMES por defecto, ver data_access.feature_importances) — NUNCA
# hardcodeado aquí: este número ha cambiado dos veces ya (6 -> 14 -> 16,
# ver CONTEXTO.md "Ampliación de features" y "Preparación de noticias
# como feature") y un texto fijo se queda desactualizado en silencio cada
# vez que se añaden features nuevas.
n_features = len(fi)
st.markdown(
    f"""
El modelo no es magia ni "sabe" de bolsa: cada día mira {n_features} datos numéricos calculados a
partir del historial de precios (y, si hay cobertura, noticias) de cada acción, y con eso decide si
cree que subirá o bajará mañana.

Esta página muestra en cuáles de esos {n_features} datos se apoya más a la hora de decidir, y en
cuáles menos — como una receta, donde unos ingredientes pesan más que otros en el resultado final.
    """
)

fig = px.bar(
    fi, x="importancia", y="etiqueta", orientation="h",
    labels={"importancia": "Peso en la decisión", "etiqueta": ""},
    color_discrete_sequence=["#1D4ED8"],
)
fig.update_layout(
    yaxis=dict(autorange="reversed"), height=380, margin=dict(l=10, r=10, t=20, b=10),
    xaxis=dict(showticklabels=False),
    plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
)
with st.container(border=True):
    st.plotly_chart(fig, width="stretch")

st.divider()

st.subheader("Qué significa cada uno")
for _, row in fi.iterrows():
    with st.container(border=True):
        col_a, col_b = st.columns([1, 3])
        col_a.metric(row["etiqueta"], f"{row['importancia']:.0%} del peso")
        col_b.write(row.get("explicacion", ""))

with st.expander("Más detalles técnicos (opcional)"):
    st.markdown(
        f"""
Modelo usado: **{"Random Forest" if model_type == "random_forest" else "Regresión logística"}**
(`{bundle.get('_model_version', '?')}`).

- **Random Forest**: combina muchos árboles de decisión sencillos y promedia sus resultados. El "peso"
  de cada variable mide cuánto ayuda, en promedio, a que esos árboles acierten.
- **Regresión logística**: una fórmula matemática más simple y directa. Aquí el "peso" es el tamaño del
  coeficiente de cada variable en esa fórmula — indica cuánto influye, pero no cómo se combina con las
  demás (a diferencia de Random Forest, no capta relaciones más complejas entre variables).

Ninguna de las {n_features} variables usa el precio de la acción en bruto (20 dólares o 900 dólares) —
todas están calculadas como porcentajes o comparaciones relativas, para que una acción cara y una barata
sean comparables entre sí. Ver `CONTEXTO.md` y `model.build_feature_matrix` para el detalle completo.
        """
    )
