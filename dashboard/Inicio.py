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

RECORTADO PARA v1 (2026-08-27, ver CONTEXTO.md "Roadmap v1 (MVP para
publicar)"): de las 8 páginas que llegó a haber, el v1 registra solo 5.
Se quitaron de la navegación (los archivos NO se borran, solo dejan de
registrarse aquí — se pueden recuperar cambiando esta lista):
- "Resumen" (views/inicio.py) — redundante con "Dashboard", que ahora es
  la página de entrada.
- "Noticias de la acción" / "Sentimiento del mercado" — dependen del
  backfill de Alpha Vantage (~9 días desde cero) y CONTEXTO.md ya
  documenta que esa señal no ayuda a predecir el precio; no bloquean el
  valor central del v1 (predicción + honestidad de resultado).
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui  # noqa: E402

st.set_page_config(page_title="Stocker", page_icon=str(ui.APP_ICON_DARK_SVG_PATH), layout="wide")

ui.inject_css()
ui.render_logo()

pages = [
    st.Page("views/dashboard.py", title="Dashboard", default=True),
    st.Page("views/predicciones.py", title="Predicciones"),
    st.Page("views/importancia_de_features.py", title="En qué se fija"),
    st.Page("views/rendimiento_del_modelo.py", title="¿Funciona de verdad?"),
    st.Page("views/seguimiento_real.py", title="Día a día"),
]
pg = st.navigation(pages, position="top")
pg.run()
