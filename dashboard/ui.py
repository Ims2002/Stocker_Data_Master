"""
dashboard/ui.py — piezas visuales compartidas por todas las páginas.

REDISEÑO 2026-09-17 (mockups "Cuatro direcciones de estilo para Stocker",
elegida la mezcla de dos):

- Modo claro  = "Fintech limpia": fondo gris muy claro, tarjetas blancas muy
  redondeadas con sombra suave, Figtree, acento verde azulado (#0E8A7E),
  gráficos de área sin rejilla vertical.
- Modo oscuro = "Terminal financiera": negro azulado, paneles planos con
  filetes finos, JetBrains Mono para cifras y texto e IBM Plex Sans
  Condensed en mayúsculas para etiquetas, acento ámbar (#F2A93B), velas
  japonesas y cinta de cotizaciones.

Los colores y tipografías de los widgets nativos (selectores, botones,
tablas, métricas) viven en `.streamlit/config.toml` ([theme.light] y
[theme.dark]). Aquí están los mismos valores para lo que Streamlit no pinta
solo: CSS de acabado, gráficos de Plotly, HTML propio (noticias, cinta) y el
medidor de sentimiento. Si se cambia un color, hay que cambiarlo en los dos
sitios.

Qué modo se usa: el que Streamlit tiene activo (`st.context.theme.type`),
que por defecto sigue la preferencia del sistema del visitante y se puede
cambiar en el menú ⋮ > Settings. Limitación conocida de Streamlit: si se
cambia el tema desde ese menú, los gráficos y el CSS de este módulo se
actualizan en la siguiente interacción (cualquier clic o cambio de filtro),
no al instante.

Logo: se mantiene el actual (wordmark STOCKER con la K de tendencia). En modo
oscuro se usa una variante con el trazo vertical y las letras en claro
(`*_dark.svg`), porque el azul marino original desaparece sobre el negro.

Uso en cada página/entrypoint:
    import ui
    ui.inject_css()
    ui.render_logo()
"""

from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
WORDMARK_SVG_PATH = _ASSETS_DIR / "stocker_wordmark.svg"
K_ICON_SVG_PATH = _ASSETS_DIR / "stocker_k_icon.svg"
WORDMARK_DARK_SVG_PATH = _ASSETS_DIR / "stocker_wordmark_dark.svg"
K_ICON_DARK_SVG_PATH = _ASSETS_DIR / "stocker_k_icon_dark.svg"
APP_ICON_DARK_SVG_PATH = _ASSETS_DIR / "stocker_app_icon_dark.svg"
APP_ICON_LIGHT_SVG_PATH = _ASSETS_DIR / "stocker_app_icon_light.svg"


# --- Paletas ----------------------------------------------------------------
# Mismos valores que [theme.light] / [theme.dark] en .streamlit/config.toml.

PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "name": "fintech",
        "bg": "#F3F4F8",
        "card": "#FFFFFF",
        "ink": "#0E1116",
        "muted": "#6B7280",
        "line": "#E6E8EE",
        "grid": "#EEF0F4",
        "accent": "#0E8A7E",
        "accent_fill": "rgba(14,138,126,0.12)",
        "up": "#0E9F6E",
        "down": "#E5484D",
        "neutral": "#9AA0AA",
        "train": "#CBD2DC",
        "highlight": "#F59E0B",
        "second": "#0E1116",
        "font": "Figtree, system-ui, sans-serif",
        "mono": "'Geist Mono', ui-monospace, monospace",
    },
    "dark": {
        "name": "terminal",
        "bg": "#07090D",
        "card": "#0E1219",
        "ink": "#D7DEE8",
        "muted": "#6E7A8C",
        "line": "#1C2330",
        "grid": "#161C27",
        "accent": "#F2A93B",
        "accent_fill": "rgba(242,169,59,0.14)",
        "up": "#2BD98A",
        "down": "#FF5470",
        "neutral": "#6E7A8C",
        "train": "#2A3342",
        "highlight": "#57C7FF",
        "second": "#57C7FF",
        "font": "'JetBrains Mono', ui-monospace, monospace",
        "mono": "'JetBrains Mono', ui-monospace, monospace",
    },
}

_SENTIMENT_KEYS = ("Bearish", "Somewhat-Bearish", "Neutral", "Somewhat-Bullish", "Bullish")


def mode() -> str:
    """"light" o "dark": el tema que Streamlit tiene activo para este
    visitante. Si la versión de Streamlit no lo expone, claro."""
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:  # noqa: BLE001 — versiones antiguas sin st.context.theme
        return "light"


def pal() -> dict[str, str]:
    return PALETTES[mode()]


def is_terminal() -> bool:
    return mode() == "dark"


def sentiment_color(label: str) -> str:
    """Color de una etiqueta de sentimiento de Alpha Vantage en el modo actual."""
    p = pal()
    return {
        "Bearish": p["down"],
        "Somewhat-Bearish": p["down"],
        "Neutral": p["neutral"],
        "Somewhat-Bullish": p["up"],
        "Bullish": p["up"],
    }.get(label, p["neutral"])


# --- Logo -------------------------------------------------------------------

def render_logo() -> None:
    """Logo de Stocker en la barra de navegación: wordmark con la K de
    tendencia y, con la barra lateral plegada, solo la K. Variante clara en
    modo terminal (ver docstring del módulo)."""
    if is_terminal():
        st.logo(str(WORDMARK_DARK_SVG_PATH), icon_image=str(K_ICON_DARK_SVG_PATH), size="medium")
    else:
        st.logo(str(WORDMARK_SVG_PATH), icon_image=str(K_ICON_SVG_PATH), size="medium")


# --- CSS --------------------------------------------------------------------

def card(key: str, **kwargs):
    """Tarjeta del tema: `st.container(border=True)` con una clave
    `card_<key>`, que es lo que el CSS usa para darle el acabado de cada modo
    (Streamlit no marca los contenedores con borde de otra forma estable)."""
    return st.container(border=True, key=f"card_{key}", **kwargs)


_CSS_COMMON = """
/* Cifras alineadas en columnas en todo el dashboard. */
[data-testid="stMetricValue"], [data-testid="stMetricDelta"], .stk-num {
    font-variant-numeric: tabular-nums;
}
[data-testid="stHeader"] { box-shadow: none; }
/* Menos aire vacío entre la barra superior y el contenido. */
[data-testid="stMainBlockContainer"] { padding-top: 3.6rem; }
/* Noticias propias (HTML): enlaces sin subrayado, heredan color. */
.stk-news a { color: inherit; text-decoration: none; }
.stk-news a:hover { text-decoration: underline; }
"""

_CSS_LIGHT = """
[data-testid="stHeader"] {{ background: {bg}; }}
/* Navegación superior en "pastilla": el enlace activo es una píldora blanca. */
[data-testid="stTopNavLink"] {{
    border-radius: 999px !important; padding: 0.35rem 0.85rem !important;
}}
[data-testid="stTopNavLink"] p {{ color: {muted} !important; font-weight: 500 !important; font-size: 0.86rem !important; }}
[data-testid="stTopNavLink"][aria-current="page"] {{
    background: {card} !important; box-shadow: 0 1px 2px rgba(16,24,40,.08);
}}
[data-testid="stTopNavLink"][aria-current="page"] p {{ color: {ink} !important; font-weight: 600 !important; }}
[data-testid="stSidebar"] {{ background: {card}; border-right: 1px solid {line}; }}

/* Tarjetas: blancas, muy redondeadas, sin borde y con sombra suave. */
div[class*="st-key-card_"] {{
    background: {card}; border: 0 !important; border-radius: 22px !important;
    box-shadow: 0 1px 2px rgba(16,24,40,.05), 0 1px 12px rgba(16,24,40,.04);
    padding: 1.1rem 1.25rem !important;
}}
h1, h2, h3 {{ letter-spacing: -0.02em; font-weight: 700 !important; }}
[data-testid="stMetricLabel"] p {{ color: {muted} !important; font-size: 0.8rem !important; font-weight: 500 !important; }}
[data-testid="stMetricValue"] {{ font-weight: 700 !important; letter-spacing: -0.02em; }}
[data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-primary"] {{ border-radius: 999px !important; }}
hr {{ border-color: {line} !important; }}
.stk-tape {{ display: none; }}
"""

_CSS_DARK = """
[data-testid="stHeader"] {{ background: #0A0D13; border-bottom: 1px solid {line}; }}
/* Navegación tipo terminal: mayúsculas condensadas; la página activa en ámbar. */
[data-testid="stTopNavLink"] {{ border-radius: 0 !important; padding: 0.3rem 0.65rem !important; }}
[data-testid="stTopNavLink"] p {{
    font-family: 'IBM Plex Sans Condensed', sans-serif !important; text-transform: uppercase;
    letter-spacing: 0.08em; font-size: 0.74rem !important; font-weight: 500 !important; color: {muted} !important;
}}
[data-testid="stTopNavLink"][aria-current="page"] {{ background: {accent} !important; }}
[data-testid="stTopNavLink"][aria-current="page"] p {{ color: {bg} !important; }}
[data-testid="stSidebar"] {{ background: {card}; border-right: 1px solid {line}; }}

/* Paneles planos con filete, sin sombra ni esquinas. */
div[class*="st-key-card_"] {{
    background: {card}; border: 1px solid {line} !important; border-radius: 2px !important;
    box-shadow: none; padding: 0.8rem 0.95rem !important;
}}
h1, h2, h3, h4 {{
    font-family: 'IBM Plex Sans Condensed', sans-serif !important; text-transform: uppercase;
    letter-spacing: 0.06em; font-weight: 600 !important;
}}
[data-testid="stMetricLabel"] p {{
    font-family: 'IBM Plex Sans Condensed', sans-serif !important; text-transform: uppercase;
    letter-spacing: 0.12em; font-size: 0.7rem !important; color: {muted} !important;
}}
[data-testid="stMetricValue"] {{ font-weight: 500 !important; }}
[data-testid="stCaptionContainer"] p {{ color: {muted}; }}
hr {{ border-color: {line} !important; }}

/* Cinta de cotizaciones (solo modo terminal, ver render_ticker_tape). */
.stk-tape {{
    display: flex; gap: 1.6rem; overflow: hidden; white-space: nowrap; font-size: 0.78rem;
    color: {muted}; border-top: 1px solid {line}; border-bottom: 1px solid {line}; padding: 0.35rem 0;
    margin: -0.5rem 0 0.4rem;
}}
.stk-tape b {{ color: {ink}; font-weight: 500; }}
"""


def inject_css() -> None:
    p = pal()
    extra = (_CSS_DARK if is_terminal() else _CSS_LIGHT).format(**p)
    # Caja del chat experto de "Contenido Premium": borde del color de acento.
    chat = (
        f'div[class*="st-key-premium_chat_box"] {{ border-color: {p["accent"]} !important; }}'
    )
    st.markdown(f"<style>{_CSS_COMMON}{extra}{chat}</style>", unsafe_allow_html=True)


# --- Gráficos (Plotly) ------------------------------------------------------

def style_fig(fig: go.Figure, height: int = 320, **layout) -> go.Figure:
    """Acabado común de todos los gráficos de Plotly según el modo:
    fondo transparente (el de la tarjeta), tipografía del tema, rejilla solo
    horizontal y muy tenue en fintech, rejilla y eje de precios a la derecha
    en terminal. `layout` sobrescribe cualquier valor."""
    p = pal()
    terminal = is_terminal()
    fig.update_layout(
        template="plotly_dark" if terminal else "plotly_white",
        height=height,
        margin=dict(l=8, r=8, t=28, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=p["font"], color=p["ink"], size=11 if terminal else 12),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            font=dict(size=10 if terminal else 11, color=p["muted"]), bgcolor="rgba(0,0,0,0)",
        ),
        hoverlabel=dict(bgcolor=p["card"], bordercolor=p["line"], font=dict(family=p["font"], color=p["ink"])),
    )
    fig.update_xaxes(
        showgrid=terminal, gridcolor=p["grid"], zeroline=False, showline=True, linecolor=p["line"],
        tickfont=dict(color=p["muted"]), title_font=dict(color=p["muted"]),
    )
    fig.update_yaxes(
        showgrid=True, gridcolor=p["grid"], zeroline=False, showline=False,
        tickfont=dict(color=p["muted"]), title_font=dict(color=p["muted"]),
        side="right" if terminal else "left",
    )
    if layout:
        fig.update_layout(**layout)
    return fig


def plotly_chart(fig: go.Figure, **kwargs) -> None:
    """`st.plotly_chart` con los colores de `style_fig` tal cual (sin que el
    tema por defecto de Streamlit los sobrescriba)."""
    st.plotly_chart(fig, width="stretch", theme=None, **kwargs)


def add_date_vline(fig, x, text: str) -> None:
    """Línea vertical punteada con etiqueta en un eje de fechas.

    Sustituye a `fig.add_vline(x=Timestamp, annotation_text=...)`: con
    algunas combinaciones de Plotly y pandas recientes, Plotly intenta sumar
    las fechas para colocar la etiqueta y pandas lo rechaza
    ("Addition/subtraction of integers ... with Timestamp is no longer
    supported"). Dibujar la línea y la etiqueta por separado evita esa suma."""
    p = pal()
    fig.add_shape(
        type="line", x0=x, x1=x, xref="x", y0=0, y1=1, yref="paper",
        line=dict(dash="dot", color=p["muted"], width=1),
    )
    fig.add_annotation(
        x=x, xref="x", y=0.99, yref="paper", text=text, showarrow=False, xanchor="left", xshift=4,
        yanchor="top", font=dict(size=10.5, color=p["muted"]),
    )


def price_chart(
    prices, *, gold_eval=None, gold_train_period=None, test_cutoff=None, prediction=None, height: int = 320,
) -> go.Figure:
    """Gráfico principal de precio del Dashboard y de Predicciones.

    - Fintech (claro): área suave del cierre, aciertos/fallos como puntos
      pequeños, periodo de entrenamiento en gris, predicción como punto con
      línea discontinua.
    - Terminal (oscuro): velas japonesas con volumen tenue al pie, marcas de
      acierto/fallo junto a cada vela y predicción como triángulo.

    `prices`: DataFrame de daily_prices (date, open, high, low, close,
    volume). `gold_eval` / `gold_train_period`: filas con `date`, `close` y
    `acierto`. `prediction`: dict con `date_predicha` y
    `predicted_target_up_down`, o None."""
    import pandas as pd

    p = pal()
    terminal = is_terminal()
    fig = go.Figure()

    if terminal:
        vmax = float(prices["volume"].max() or 1)
        lo, hi = float(prices["low"].min()), float(prices["high"].max())
        span = hi - lo or 1.0
        # Volumen dibujado en la franja inferior del eje de precios (sin
        # subplots, para que el eje de fechas sea uno solo).
        base = lo - span * 0.28
        vol_h = span * 0.22
        fig.add_trace(go.Bar(
            x=prices["date"], y=prices["volume"] / vmax * vol_h, base=base, name="Volumen",
            marker=dict(color=[p["up"] if c >= o else p["down"] for o, c in zip(prices["open"], prices["close"])]),
            opacity=0.28, showlegend=False, hoverinfo="skip",
        ))
        fig.add_trace(go.Candlestick(
            x=prices["date"], open=prices["open"], high=prices["high"], low=prices["low"], close=prices["close"],
            name="Precio", increasing=dict(line=dict(color=p["up"], width=1), fillcolor=p["bg"]),
            decreasing=dict(line=dict(color=p["down"], width=1), fillcolor=p["down"]),
            showlegend=False,
        ))
        fig.update_yaxes(range=[base, hi + span * 0.08])
        fig.update_xaxes(rangeslider_visible=False)
        last = float(prices["close"].iloc[-1])
        fig.add_hline(y=last, line=dict(color=p["accent"], width=1, dash="dot"))
    else:
        lo = float(prices["close"].min())
        hi = float(prices["close"].max())
        pad = (hi - lo) * 0.08 or 1.0
        fig.add_trace(go.Scatter(
            x=prices["date"], y=[lo - pad] * len(prices), mode="lines", line=dict(width=0),
            showlegend=False, hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=prices["date"], y=prices["close"], mode="lines", name="Cierre",
            line=dict(color=p["accent"], width=2.4, shape="spline", smoothing=0.3),
            fill="tonexty", fillcolor=p["accent_fill"], showlegend=False,
        ))
        fig.update_yaxes(range=[lo - pad, hi + pad], tickprefix="$")

    if test_cutoff is not None:
        add_date_vline(fig, test_cutoff, "EXAMEN REAL →" if terminal else "examen real →")

    def _y(df, where):
        if not terminal:
            return df["close"]
        rows = prices.set_index(pd.to_datetime(prices["date"]))
        dates = pd.to_datetime(df["date"])
        col = rows["low"] if where == "below" else rows["high"]
        span_ = float(prices["high"].max() - prices["low"].min()) or 1.0
        off = span_ * 0.025
        return [float(col.get(d, float("nan"))) + (-off if where == "below" else off) for d in dates]

    if gold_train_period is not None and not gold_train_period.empty:
        fig.add_trace(go.Scatter(
            x=gold_train_period["date"], y=_y(gold_train_period, "below"), mode="markers",
            name="Entrenamiento (no cuenta)", marker=dict(color=p["train"], size=4),
        ))
    if gold_eval is not None and not gold_eval.empty:
        ok, ko = gold_eval[gold_eval["acierto"]], gold_eval[~gold_eval["acierto"]]
        fig.add_trace(go.Scatter(
            x=ok["date"], y=_y(ok, "below"), mode="markers", name="Acertó",
            marker=dict(color=p["highlight"] if terminal else p["up"], size=4 if terminal else 6),
        ))
        fig.add_trace(go.Scatter(
            x=ko["date"], y=_y(ko, "above"), mode="markers", name="Falló",
            marker=dict(color=p["accent"] if terminal else p["down"], size=5 if terminal else 6, symbol="x-thin",
                        line=dict(width=1.5, color=p["accent"] if terminal else p["down"])),
        ))

    if prediction and prediction.get("predicted_target_up_down") is not None:
        sube = prediction["predicted_target_up_down"] == 1
        color = p["up"] if sube else p["down"]
        x_pred = pd.Timestamp(prediction["date_predicha"])
        y_last = float(prices["close"].iloc[-1])
        fig.add_trace(go.Scatter(
            x=[pd.Timestamp(prices["date"].iloc[-1]), x_pred], y=[y_last, y_last], mode="lines",
            line=dict(color=color, width=2, dash="dot"), showlegend=False, hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=[x_pred], y=[y_last], mode="markers+text",
            name=f"Predicción {prediction['date_predicha']}",
            marker=dict(
                color=color, size=13 if terminal else 12,
                symbol=("triangle-up" if sube else "triangle-down") if terminal else "circle",
                line=dict(color=p["card"], width=2),
            ),
            text=[("▲ SUBE" if sube else "▼ BAJA") if terminal else ("Sube" if sube else "Baja")],
            textposition="top left", textfont=dict(color=color, size=11),
        ))

    style_fig(fig, height=height)
    return fig


# --- Piezas HTML ------------------------------------------------------------

def render_ticker_tape(rows) -> None:
    """Cinta de cotizaciones del modo terminal: ticker, último cierre y
    variación. `rows`: iterable de (ticker, cierre, variación relativa). En
    modo claro no se pinta (el CSS la oculta y aquí no se genera)."""
    if not is_terminal():
        return
    p = pal()
    items = []
    for ticker, close, change in rows:
        color = p["up"] if change >= 0 else p["down"]
        items.append(f'<span><b>{ticker}</b> {close:,.2f} <span style="color:{color}">{change:+.2%}</span></span>')
    st.markdown(f'<div class="stk-tape stk-num">{"".join(items)}</div>', unsafe_allow_html=True)


# --- Avisos compartidos (revisión de auditoría, 16/09/2026) -----------------

DISCLAIMER = (
    "Stocker es un proyecto académico. Las predicciones son la salida de un modelo estadístico que no "
    "supera de forma consistente a reglas simples: no son una recomendación de inversión."
)


def render_disclaimer() -> None:
    """Aviso legal en todas las páginas (auditoría, U1)."""
    st.caption(f"⚖️ {DISCLAIMER}")


def render_data_freshness(latest_date, pipeline_status: dict | None = None) -> None:
    """Fecha de los datos y resultado de la última ejecución del pipeline
    (auditoría, A4)."""
    if latest_date is None:
        return
    texto = f"Datos de mercado hasta el **{latest_date:%d/%m/%Y}** (cierre de EE. UU.)."
    if pipeline_status and pipeline_status.get("finished_at"):
        fin = str(pipeline_status["finished_at"]).replace("T", " ")[:16]
        errores = [s["step"] for s in pipeline_status.get("steps", []) if s.get("rc")]
        if pipeline_status.get("result") == "database_corrupt":
            texto += " ⚠️ La última actualización se detuvo: la base de datos necesita reparación."
        elif errores:
            texto += f" Última actualización {fin}, con avisos en: {', '.join(errores)}."
        else:
            texto += f" Última actualización {fin}, sin errores."
    st.caption(texto)
