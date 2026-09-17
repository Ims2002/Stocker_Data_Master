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

AÑADIDA UNA 6ª PÁGINA 2026-08-31 (ver CONTEXTO.md, "Contenido premium:
página en el dashboard"): "Contenido Premium" (views/premium.py), fuera
del recorte de arriba porque no es una página que se quitara y se
recupere, es nueva — vista previa del futuro apartado de pago, todavía
sin login ni cobro real detrás.

AÑADIDA UNA 7ª PÁGINA 2026-09-05 (ver CONTEXTO.md, "Comparador de
gráficos: dos acciones en un mismo gráfico"): "Comparador"
(views/comparador.py) — parte del apartado free, independiente de
predicción/backtest, deja elegir dos tickers y ver su precio normalizado
en un mismo gráfico.

CAMBIOS 2026-09-08 (ver CONTEXTO.md, "Quitar '¿Funciona de verdad?' y
profundizar en noticias desde el Dashboard"):
- Se QUITA "¿Funciona de verdad?" (views/rendimiento_del_modelo.py) de
  la navegación — el archivo NO se borra, solo deja de registrarse aquí,
  mismo criterio que el resto de páginas recortadas del v1.
- Se REINCORPORA "Noticias de la acción" (views/sentimiento_por_accion.py)
  — el motivo por el que se había quitado en el recorte del v1 de arriba
  (backfill de noticias todavía en marcha) ya no aplica: el backfill
  está completo (208/208 tickers). Reincorporada porque el Dashboard solo
  enseña un recorte de 4 titulares y el usuario pidió poder profundizar
  desde ahí — ver el botón "Ver todas las noticias →" en
  views/dashboard.py. "Sentimiento del mercado"
  (views/sentimiento_del_mercado.py) se queda fuera por ahora, no se pidió.

REINCORPORADA OTRA VEZ 2026-09-09 (ver CONTEXTO.md, "Calibración de
predicted_probability" y "Leakage en el split de horizontes 5/20",
feedback de un tutor del TFM): "¿Funciona de verdad?"
(views/rendimiento_del_modelo.py) vuelve a la navegación — motivo
distinto al que la quitó el día anterior. Ahora incluye una sección de
calibración (curva de fiabilidad + Brier score sobre el test real) que
responde directamente al feedback de no vender `predicted_probability`
como "confianza" sin comprobarlo. También gana selector de horizonte
(antes solo mostraba el horizonte día).

REVISIÓN DE AUDITORÍA 2026-09-16: aviso legal común a todas las páginas
(`ui.render_disclaimer`, hallazgo U1).
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
    st.Page("views/comparador.py", title="Comparador"),
    st.Page("views/importancia_de_features.py", title="En qué se fija"),
    st.Page("views/rendimiento_del_modelo.py", title="¿Funciona de verdad?"),
    st.Page("views/seguimiento_real.py", title="Día a día"),
    st.Page("views/sentimiento_por_accion.py", title="Noticias de la acción"),
    st.Page("views/premium.py", title="Contenido Premium", icon="🔒"),
]
pg = st.navigation(pages, position="top")
# Aviso legal antes del contenido (auditoría, U1): muchas páginas terminan
# con st.stop(), así que un aviso colocado después de pg.run() no llegaría a
# pintarse en esos casos.
ui.render_disclaimer()
pg.run()
