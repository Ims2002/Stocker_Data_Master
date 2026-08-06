"""
dashboard/ui.py — piezas visuales compartidas por todas las páginas
(navegación superior + CSS del tema minimalista blanco/azul/negro).

Diseño aprobado por el usuario a partir de mockups (2026-08-06, ver
CONTEXTO.md). Los colores base viven en .streamlit/config.toml — si cambian
ahí, hay que revisar también el CSS de aquí (usa los mismos valores a mano
porque Streamlit no expone las variables de tema dentro de CSS inyectado).

Uso en cada página/entrypoint:
    import ui
    ui.inject_css()
"""

from __future__ import annotations

import streamlit as st

# Mismos valores que .streamlit/config.toml — repetidos aquí porque el CSS
# inyectado no puede leer el theme.toml directamente.
_AZUL = "#1D4ED8"
_NEGRO = "#0B0F19"
_GRIS_TEXTO = "#9CA3AF"
_GRIS_LINEA = "#EEF0F2"
_BORDE = "#E5E7EB"

_LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="88" height="22">'
    '<text x="0" y="16" font-family="sans-serif" font-size="15" '
    f'font-weight="600" fill="{_NEGRO}">Stocker</text></svg>'
)


def render_logo() -> None:
    """Wordmark "Stocker" en la esquina superior izquierda de la barra de
    navegación (mismo lugar donde iría el logo en la maqueta aprobada)."""
    st.logo(_LOGO_SVG, size="medium")


def inject_css() -> None:
    st.markdown(
        f"""
        <style>
        /* Cabecera: fondo blanco, línea muy fina abajo en vez de sombra */
        [data-testid="stHeader"] {{
            background: #FFFFFF;
            border-bottom: 0.5px solid {_BORDE};
        }}

        /* Oculta lo que sobra de la barra por defecto de Streamlit (menú
           hamburguesa, botón Deploy) — ya reforzado por
           client.toolbarMode = "minimal" en config.toml, esto es un
           refuerzo por si esa opción cambia de comportamiento entre
           versiones. */
        [data-testid="stMainMenu"], [data-testid="stAppDeployButton"] {{
            display: none;
        }}

        /* Navegación superior: texto pequeño, gris cuando no está
           seleccionada, negro + subrayado azul cuando sí (aria-current
           ="page" lo pone Streamlit automáticamente en la página activa). */
        [data-testid="stPageLink"] p {{
            font-size: 13px !important;
            color: {_GRIS_TEXTO} !important;
            font-weight: 400 !important;
        }}
        [data-testid="stPageLink"][aria-current="page"] p {{
            color: {_NEGRO} !important;
            font-weight: 500 !important;
        }}
        [data-testid="stPageLink"][aria-current="page"] {{
            border-bottom: 2px solid {_AZUL};
        }}

        /* Tarjetas (st.container(border=True)): esquinas más suaves, borde
           muy sutil en vez del gris marcado por defecto. */
        [data-testid="stVerticalBlockBorderWrapper"] {{
            border-color: {_GRIS_LINEA} !important;
            border-radius: 10px !important;
        }}

        /* Separadores (st.divider): línea de 0.5px en vez de 1px, mismo
           gris muy claro que en la maqueta. */
        hr {{
            border-color: {_GRIS_LINEA} !important;
        }}

        /* Métricas (st.metric): etiqueta gris pequeña, valor en negro sin
           negrita extrema para que combine con el resto de la tipografía. */
        [data-testid="stMetricLabel"] p {{
            color: {_GRIS_TEXTO} !important;
            font-size: 12px !important;
        }}
        [data-testid="stMetricValue"] {{
            color: {_NEGRO} !important;
            font-weight: 500 !important;
        }}

        /* Botón "?" de ayuda de los widgets: apagarlo visualmente un poco
           para que no compita con el resto (sigue siendo funcional). */
        [data-testid="stTooltipIcon"] {{
            color: {_GRIS_TEXTO} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
