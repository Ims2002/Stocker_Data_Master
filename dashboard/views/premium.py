"""
dashboard/views/premium.py — "Contenido Premium": vista previa del futuro
apartado de pago de Stocker (2026-08-31, ver CONTEXTO.md "Apartado de
pago: análisis en detalle de hyperscalers" y "Contenido premium: página
en el dashboard").

FASE ACTUAL: solo contenido, sin cobro ni sistema de usuarios todavía —
cualquiera que abra el dashboard puede ver esta página tal cual. Cuando
exista de verdad un apartado de pago (login + pasarela), esta página es
el punto donde habría que añadir el control de acceso — de momento no
hay nada que lo bloquee, a propósito (ver esa misma entrada de
CONTEXTO.md: contenido primero, infraestructura de pago después).

Lee directamente los archivos Markdown de `docs/premium/` — fuera del
pipeline de datos, no toca `src/` ni la base de datos para esto (salvo
para nombre/logo de cada ticker, vía data_access, igual que el resto del
dashboard). Si el archivo de un ticker todavía no existe, se muestra una
tarjeta de "Próximamente" en su lugar, en vez de romper o dejar un hueco
vacío — así esta página no requiere tocar código cada vez que se añade
un nuevo análisis, solo añadir el `.md` correspondiente.
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_access as da  # noqa: E402

engine = da.get_engine()

_PREMIUM_DIR = Path(__file__).resolve().parent.parent.parent / "docs" / "premium"

HYPERSCALERS = ["AMZN", "MSFT", "GOOGL", "META"]

# "Próximamente": todavía sin material del usuario ni archivo .md. NVDA sí
# forma parte del universo de 208 tickers de Stocker (tiene metadatos y
# logo vía data_access); SPX (el índice S&P 500) no es una acción
# individual, así que no está en `stocks` — de ahí el nombre a mano en
# vez de tirar de get_ticker_metadata como en los hyperscalers.
PROXIMAMENTE = [
    {"ticker": "NVDA", "nombre": "NVIDIA Corporation"},
    {"ticker": "SPX", "nombre": "S&P 500"},
]


@st.cache_data(ttl=300)
def _read_analysis(path_str: str) -> str | None:
    path = Path(path_str)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


st.markdown(
    '<div style="margin-top:4px;">'
    '<span style="font-size:1.4rem; font-weight:600;">Contenido Premium</span></div>',
    unsafe_allow_html=True,
)
st.caption(
    "Vista previa de lo que será el futuro apartado de pago de Stocker: análisis en detalle, "
    "acción por acción, más allá de la predicción del modelo. Contenido informativo/educativo, "
    "no asesoramiento financiero personalizado — de momento en fase de contenido, sin ningún "
    "cobro ni cuenta de usuario todavía."
)

st.subheader("Hyperscalers")
tabs = st.tabs(HYPERSCALERS)
for ticker, tab in zip(HYPERSCALERS, tabs):
    with tab:
        contenido = _read_analysis(str(_PREMIUM_DIR / "hyperscalers" / f"{ticker}.md"))
        if contenido:
            st.markdown(contenido)
            continue
        meta = da.get_ticker_metadata(engine, ticker)
        logo_url = da.ticker_logo_url(ticker)
        logo_html = (
            f'<img src="{logo_url}" width="32" height="32" '
            'style="border-radius:6px; vertical-align:middle; margin-right:10px;">'
        ) if logo_url else ""
        nombre = html.escape(meta.get("nombre") or ticker)
        with st.container(border=True):
            st.markdown(
                f'<div>{logo_html}<span style="font-size:1.05rem; vertical-align:middle;">'
                f'{nombre} · <strong>#{ticker}</strong></span></div>',
                unsafe_allow_html=True,
            )
            st.caption("Análisis en preparación — todavía no está disponible.")

st.divider()
st.subheader("Próximamente")
cols = st.columns(len(PROXIMAMENTE))
for item, col in zip(PROXIMAMENTE, cols):
    with col:
        contenido = _read_analysis(str(_PREMIUM_DIR / "otros" / f"{item['ticker']}.md"))
        with st.container(border=True):
            st.markdown(f"**{html.escape(item['nombre'])}** · `{item['ticker']}`")
            if contenido:
                st.markdown(contenido)
            else:
                st.caption("Próximo análisis en detalle — todavía no disponible.")
