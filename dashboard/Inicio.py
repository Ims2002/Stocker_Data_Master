"""
dashboard/Inicio.py — punto de entrada del dashboard de Stocker.

Lanzar con:
    python -m streamlit run dashboard/Inicio.py

Este archivo actúa como enrutador (st.navigation) y aplica el tema visual
compartido (ui.py) — el contenido real de cada sección vive en
views/inicio.py, views/predicciones.py, etc. (¿por qué "views" y no
"pages"? ver la nota al principio de views/inicio.py). Navegación arriba
en vez de sidebar, a propósito: interfaz minimalista aprobada por el
usuario a partir de mockups (2026-08-06, ver CONTEXTO.md).

Solo lectura (ver data_access.py): el dashboard nunca escribe en la base de
datos ni entrena/mueve modelos — eso lo hace src/ (download.py, load.py,
gold.py, model.py, predict.py, news.py). Si los datos parecen
desactualizados, el problema está en esos scripts (o en si corrió
run_news_daily.bat), no aquí.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui  # noqa: E402

st.set_page_config(page_title="Stocker", page_icon="📈", layout="wide")

ui.inject_css()
ui.render_logo()

pages = [
    st.Page("views/inicio.py", title="Inicio", default=True),
    st.Page("views/predicciones.py", title="Predicciones"),
    st.Page("views/importancia_de_features.py", title="En qué se fija"),
    st.Page("views/rendimiento_del_modelo.py", title="¿Funciona de verdad?"),
]
pg = st.navigation(pages, position="top")
pg.run()
