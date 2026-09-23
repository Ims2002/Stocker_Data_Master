# Rediseño visual (17/09/2026)

Mezcla de dos de las cuatro direcciones exploradas en los mockups:

| Modo | Dirección | Rasgos |
|------|-----------|--------|
| Claro | Fintech limpia | Fondo #F3F4F8, tarjetas blancas muy redondeadas con sombra suave, Figtree, acento #0E8A7E, gráfico de área |
| Oscuro | Terminal financiera | Fondo #07090D, paneles planos con filete, JetBrains Mono + IBM Plex Sans Condensed en mayúsculas, acento #F2A93B, velas japonesas, cinta de cotizaciones |

## Cómo se elige el modo

Streamlit sigue la preferencia de claro u oscuro del sistema de cada visitante.
Cada persona puede cambiarlo en el menú **⋮ > Settings > Theme** (por eso
`client.toolbarMode` pasa de `minimal` a `viewer`).

Streamlit solo reenvía el tema al servidor cuando el script se vuelve a
ejecutar, así que por sí solo cambiaría los widgets al instante y dejaría los
gráficos y los acabados propios con el tema anterior hasta la siguiente
interacción (ver streamlit#11920). `ui.sync_theme()` (23/09/2026) lo resuelve:
un iframe de 0 píxeles compara el tema activo en el navegador con el que se
usó al pintar y, si no coinciden, pulsa un botón oculto que provoca una nueva
ejecución. Se marca el intento en `sessionStorage`, así que como mucho se
fuerza una ejecución por cambio de tema — nunca un bucle.

## Dónde está cada cosa

- `.streamlit/config.toml`: colores, tipografías y radios de los widgets nativos
  en `[theme.light]` y `[theme.dark]`.
- `dashboard/ui.py`:
  - `PALETTES`: los mismos colores, para lo que Streamlit no pinta solo.
  - `inject_css()`: acabado de navegación, tarjetas y métricas de cada modo.
  - `card(key)`: tarjeta del tema. Úsala en lugar de `st.container(border=True)`.
  - `sync_theme()`: repintado inmediato al cambiar de tema (ver arriba). Se
    llama una vez en `dashboard/Inicio.py`, justo después de `inject_css()`.
  - `style_fig()` y `plotly_chart()`: estilo común de los gráficos de Plotly.
  - `price_chart()`: gráfico principal (área en claro, velas en oscuro).
  - `render_ticker_tape()`: cinta de cotizaciones del modo terminal.
- `dashboard/assets/stocker_wordmark_dark.svg` y `stocker_k_icon_dark.svg`:
  logo con el trazo vertical y las letras en claro para el modo oscuro.

Para cambiar un color, hay que cambiarlo en `config.toml` y en `PALETTES`.

## Requisitos

Streamlit 1.50 o posterior (`python -m pip install -U "streamlit>=1.50"`). Con
versiones anteriores se ignoran las secciones por modo y las fuentes por URL.
