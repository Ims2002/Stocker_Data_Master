"""
dashboard/views/inicio.py — contenido de la página de inicio. El enrutado
(st.navigation) y el tema visual viven en Inicio.py (entrypoint); este
archivo solo pinta el contenido.

NOTA: vive en views/, no en pages/. Streamlit trata cualquier carpeta
llamada "pages" junto al entrypoint como app multipágina "clásica" (con
navegación automática por sidebar) incluso cuando el entrypoint usa
st.navigation() explícitamente — y al intentar registrar ambas cosas a la
vez, colisiona (el propio Inicio.py se auto-registra como página con la
ruta "Inicio", igual que este archivo, y Streamlit lo rechaza por ruta
duplicada). Usar un nombre de carpeta distinto evita ese comportamiento
heredado por completo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_access as da  # noqa: E402


@st.cache_data(ttl=300)
def _overview():
    engine = da.get_engine()
    tickers = da.list_tickers(engine)
    with engine.begin() as conn:
        from sqlalchemy import func, select
        import db as dbmod

        max_date = conn.execute(select(func.max(dbmod.daily_prices.c.date))).scalar()
        n_predictions = conn.execute(select(func.count()).select_from(dbmod.predictions)).scalar()
        n_news = conn.execute(select(func.count()).select_from(dbmod.news_articles)).scalar()
    return {
        "n_tickers": len(tickers),
        "max_date": max_date,
        "n_predictions": n_predictions,
        "n_news": n_news,
    }


st.caption(
    "Predicción de dirección (sube/baja) del cierre del día siguiente para un universo de acciones de EE. UU. "
    "— proyecto de máster orientado a producción real. Ver metodología completa en CONTEXTO.md."
)

try:
    overview = _overview()
except Exception as exc:  # noqa: BLE001
    st.error(
        "No se ha podido leer la base de datos (`data/stocker.db`). ¿Ya ejecutaste el pipeline "
        f"(download.py → load.py → gold.py → model.py → predict.py)? Detalle: {exc}"
    )
    st.stop()

with st.container(border=True):
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Tickers con datos", overview["n_tickers"])
    col2.metric("Último día cargado", str(overview["max_date"]) if overview["max_date"] else "—")
    col3.metric("Predicciones guardadas", overview["n_predictions"])
    col4.metric("Artículos de noticias", overview["n_news"])

st.divider()

st.subheader("Cómo leer este dashboard")
st.markdown(
    """
- **Predicciones** — elige una acción y compara su historial real con lo que el modelo habría dicho cada
  día si hubiera estado funcionando entonces, más la predicción para la próxima sesión.
- **En qué se fija el modelo** — qué datos pesan más a la hora de decidir si una acción sube o baja.
- **¿Funciona de verdad?** — el modelo puesto a prueba contra dos formas simples de adivinar. Si no les
  gana, se muestra igual: es un resultado honesto, no un fallo (más detalle en CONTEXTO.md).
    """
)

st.info(
    "El horizonte de predicción es actualmente **solo a 1 día** (la sesión de mercado siguiente). El selector "
    "de semana/mes que verás en *Predicciones* está preparado para el futuro, pero todavía no hay modelos "
    "entrenados a esos horizontes.",
    icon="ℹ️",
)
