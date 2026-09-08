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

CHAT EXPERTO (2026-08-31, ver CONTEXTO.md "Chat experto sobre earnings:
viabilidad y diseño"): debajo del análisis de cada ticker con contenido
disponible, un chat (`premium_chat.py`) responde preguntas usando ese
análisis + los documentos oficiales de `<TICKER>_fuentes/` como único
contexto. Se deshabilita solo (con aviso, sin romper el resto de la
página) si no hay `ANTHROPIC_API_KEY` configurada.
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_access as da  # noqa: E402
import premium_chat  # noqa: E402

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


def _render_chat(ticker: str, nombre: str) -> None:
    """Chat experto debajo del análisis de `ticker` (2026-08-31, ver
    CONTEXTO.md "Chat experto sobre earnings: viabilidad y diseño" y
    `dashboard/premium_chat.py` para el diseño completo: sin RAG
    vectorial, corpus = análisis + documentos oficiales, nunca la
    transcripción del vídeo de terceros, con límites de uso por sesión
    y globales por día porque cada pregunta tiene coste real de API).

    ESTÉTICA (2026-09-01, petición del usuario): la conversación vive en
    un `st.container(height=420, border=True, autoscroll=True)` — caja
    acotada con scroll propio en vez de dejar que los mensajes empujen
    el `st.chat_input` cada vez más abajo de una página ya muy larga
    (el análisis completo va justo encima). `autoscroll=True` hace que
    el flujo vaya de abajo hacia arriba: el mensaje más reciente queda
    siempre visible junto al input, el historial se desplaza hacia
    arriba con el scroll. Antes de escribir nada, la caja muestra un
    estado vacío con más presencia visual (icono + texto) y unas
    preguntas sugeridas como botones — en vez de quedarse en blanco."""
    st.divider()
    st.markdown("**Chat experto sobre estos resultados**")

    if not premium_chat.chat_available():
        st.info(
            "El chat todavía no está configurado (falta `ANTHROPIC_API_KEY` en el entorno) — "
            "el resto del análisis funciona igual."
        )
        return

    corpus = premium_chat.build_corpus(ticker)
    if corpus is None:
        return  # no debería pasar si ya hay análisis, pero por si acaso

    history_key = f"_premium_chat_history_{ticker}"
    if history_key not in st.session_state:
        st.session_state[history_key] = []
    history: list[dict] = st.session_state[history_key]

    restantes = max(0, premium_chat.max_per_session() - premium_chat.session_usage(ticker))
    st.caption(
        f"Responde solo con el comunicado de resultados, la transcripción de la earnings call y "
        f"el análisis de arriba — no es asesoramiento de inversión personalizado. "
        f"Preguntas restantes en esta sesión: {restantes}."
    )

    chat_box = st.container(
        height=420, border=True, autoscroll=True, key=f"premium_chat_box_{ticker}"
    )
    with chat_box:
        if not history:
            st.markdown(
                f"""
                <div style="text-align:center; padding:64px 20px;">
                    <div style="font-size:1.05rem; color:#0B0F19; font-weight:600;">
                        Pregúntame lo que quieras sobre los resultados de {html.escape(ticker)}
                    </div>
                    <div style="font-size:0.85rem; color:#9CA3AF; margin-top:6px; max-width:380px;
                                margin-left:auto; margin-right:auto;">
                        Respondo solo con el comunicado de resultados, la earnings call y el
                        análisis de esta pestaña.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            for msg in history:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

    sugerida = None
    if not history:
        cols = st.columns(3)
        for texto, col in zip(
            [
                "Resúmeme los resultados en 3 frases",
                "¿Cuáles son los principales riesgos de este trimestre?",
                "Explícame el RPO/backlog de forma sencilla",
            ],
            cols,
        ):
            if col.button(texto, key=f"_premium_chat_sug_{ticker}_{texto}", width="stretch"):
                sugerida = texto

    aviso = premium_chat.limits_reached(ticker)
    pregunta = st.chat_input(f"Pregunta algo sobre los resultados de {ticker}...", disabled=bool(aviso))
    pregunta = pregunta or sugerida
    if aviso:
        st.caption(aviso)
        return

    if pregunta:
        history.append({"role": "user", "content": pregunta})
        with chat_box:
            with st.chat_message("user"):
                st.markdown(pregunta)
            with st.chat_message("assistant"):
                with st.spinner("Pensando..."):
                    try:
                        respuesta = premium_chat.ask(ticker, nombre, corpus, history)
                    except Exception as exc:  # noqa: BLE001 — cualquier fallo de la API se muestra, no rompe la página
                        respuesta = f"No se ha podido obtener respuesta ahora mismo ({exc}). Inténtalo de nuevo en un momento."
                st.markdown(respuesta)
        history.append({"role": "assistant", "content": respuesta})
        st.rerun()


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
        meta = da.get_ticker_metadata(engine, ticker)
        nombre_plano = meta.get("nombre") or ticker
        logo_url = da.ticker_logo_url(ticker)
        logo_html = (
            f'<img src="{logo_url}" width="32" height="32" '
            'style="border-radius:6px; vertical-align:middle; margin-right:10px;">'
        ) if logo_url else ""
        nombre = html.escape(nombre_plano)

        contenido = _read_analysis(str(_PREMIUM_DIR / "hyperscalers" / f"{ticker}.md"))
        if contenido:
            # Cabecera con logo + nombre antes del análisis (2026-08-31,
            # petición del usuario, aplicada a todos los hyperscalers de
            # este bucle — no solo a MSFT — para que cualquier análisis
            # futuro la lleve automáticamente sin tocar código otra vez).
            st.markdown(
                f'<div style="margin-bottom:10px;">{logo_html}'
                f'<span style="font-size:1.05rem; vertical-align:middle;">'
                f'{nombre} · <strong>#{ticker}</strong></span></div>',
                unsafe_allow_html=True,
            )
            st.markdown(contenido)
            _render_chat(ticker, nombre_plano)
            continue
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
