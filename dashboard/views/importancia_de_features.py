"""
dashboard/views/importancia_de_features.py — qué mira el modelo para
decidir, explicado en lenguaje llano (no solo "claro" para alguien con
formación técnica — ver CONTEXTO.md, feedback del usuario 2026-08-05).
Es UNA importancia global, compartida por todos los tickers — no hay una
por acción, porque el modelo no usa el ticker como feature (ver
model.py, build_feature_matrix). Vive en views/, no pages/ — ver nota en
views/inicio.py sobre por qué.

REVISIÓN DE AUDITORÍA 2026-09-16 (M2): selector de horizonte (antes solo el
día) y, en modelos entrenados tras la revisión, importancia por permutación
sobre el test en lugar de la MDI de Random Forest.
"""

from __future__ import annotations

import sys
from pathlib import Path

import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_access as da  # noqa: E402
import ui  # noqa: E402

st.title("En qué se fija el modelo para decidir")

HORIZON_LABELS = {1: "Día (mañana)", 5: "Semana (~5 sesiones)", 20: "Mes (~20 sesiones)"}
_LABEL_TO_HORIZON = {v: k for k, v in HORIZON_LABELS.items()}
horizonte_label = st.segmented_control(
    "Horizonte", list(HORIZON_LABELS.values()), default=HORIZON_LABELS[1], key="importancia_horizonte",
)
horizon = _LABEL_TO_HORIZON[horizonte_label] if horizonte_label else 1


@st.cache_resource
def _model_bundle(horizon: int, model_version: str):
    # model_version solo forma parte de la clave de caché (auditoría, M4).
    return da.load_latest_model(horizon=horizon)


_version = da.latest_model_version(horizon)
if _version is None:
    st.info(f"No hay ningún modelo entrenado para este horizonte. Ejecuta `python src/model.py --horizon {horizon}`.")
    st.stop()
bundle = _model_bundle(horizon, _version)

fi = da.feature_importances(bundle)
model_type = bundle.get("model_type", "desconocido")

if fi is None:
    st.warning("Este modelo no permite calcular en qué se fija más o menos.")
    st.stop()
metodo = fi.attrs.get("metodo", "mdi")

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
    color_discrete_sequence=[ui.pal()["accent"]],
)
ui.style_fig(fig, height=400)
fig.update_layout(yaxis=dict(autorange="reversed", side="left"), xaxis=dict(showticklabels=False, showgrid=False))
with ui.card("importancia"):
    ui.plotly_chart(fig)

if metodo == "permutacion":
    st.caption(
        "Medido sobre el examen real (datos que el modelo no vio al entrenar): cuánto empeora el acierto "
        "si se desordena cada dato. Un peso cercano a cero o negativo significa que ese dato no le ayuda."
    )
else:
    st.caption(
        "ℹ️ Este modelo se entrenó antes de la revisión de auditoría: el peso es la importancia interna del "
        "Random Forest, que tiende a favorecer datos con muchos valores distintos y se mide sobre el "
        "entrenamiento. Reentrena para ver la importancia medida sobre el examen real."
    )

st.divider()

st.subheader("Qué significa cada uno")
for i, (_, row) in enumerate(fi.iterrows()):
    with ui.card(f"feature_{i}"):
        col_a, col_b = st.columns([1, 3])
        valor = (
            f"{row['importancia'] * 100:+.2f} pts de acierto" if metodo == "permutacion"
            else f"{row['importancia']:.0%} del peso"
        )
        col_a.metric(row["etiqueta"], valor)
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
