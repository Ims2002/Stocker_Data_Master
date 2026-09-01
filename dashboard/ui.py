"""
dashboard/ui.py — piezas visuales compartidas por todas las páginas
(navegación superior + CSS del tema minimalista blanco/azul/negro).

Diseño aprobado por el usuario a partir de mockups (2026-08-06, ver
CONTEXTO.md). Los colores base viven en .streamlit/config.toml — si cambian
ahí, hay que revisar también el CSS de aquí (usa los mismos valores a mano
porque Streamlit no expone las variables de tema dentro de CSS inyectado).

Identidad de marca (2026-08-28, ver CONTEXTO.md "Identidad de marca: logos
del artifact de claude.ai"): logotipo real a partir de los assets que trajo
el usuario desde un artifact de claude.ai (icono "K" tipo gráfico de
velas/tendencia + wordmark "STOCKER"), en vez del texto sencillo que había
antes. Los ficheros viven en `dashboard/assets/` — ver ese directorio y
`assets/generate_wordmark.py` para el porqué de regenerar el wordmark como
contornos vectoriales en vez de reusar el HTML+Google-Fonts original.

Uso en cada página/entrypoint:
    import ui
    ui.inject_css()
    ui.render_logo()
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

# Mismos valores que .streamlit/config.toml — repetidos aquí porque el CSS
# inyectado no puede leer el theme.toml directamente.
_AZUL = "#1D4ED8"
_AZUL_MARINO = "#1E3A8A"
_NEGRO = "#0B0F19"
_GRIS_TEXTO = "#9CA3AF"
_GRIS_LINEA = "#EEF0F2"
_BORDE = "#E5E7EB"

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
WORDMARK_SVG_PATH = _ASSETS_DIR / "stocker_wordmark.svg"
K_ICON_SVG_PATH = _ASSETS_DIR / "stocker_k_icon.svg"
APP_ICON_DARK_SVG_PATH = _ASSETS_DIR / "stocker_app_icon_dark.svg"
APP_ICON_LIGHT_SVG_PATH = _ASSETS_DIR / "stocker_app_icon_light.svg"


def render_logo() -> None:
    """Logo real de Stocker en la esquina superior izquierda de la barra de
    navegación: wordmark "STOCKER" (con el icono de tendencia sustituyendo
    la "K") cuando la barra lateral está abierta, y solo el icono "K"
    cuando está colapsada — mismo patrón que cualquier app SaaS con logo +
    icono compacto (ver CONTEXTO.md)."""
    st.logo(str(WORDMARK_SVG_PATH), icon_image=str(K_ICON_SVG_PATH), size="medium")


def inject_css() -> None:
    st.markdown(
        f"""
        <style>
        /* Cabecera: fondo blanco, línea inferior en degradado azul marino
           que se desvanece a blanco en el último cuarto del ancho de
           pantalla (en vez de la línea gris fina plana de antes). */
        [data-testid="stHeader"] {{
            background: #FFFFFF;
            border-bottom: 3px solid transparent;
            border-image: linear-gradient(
                to right, {_AZUL_MARINO} 0%, {_AZUL_MARINO} 75%, rgba(255,255,255,0) 100%
            ) 1;
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
