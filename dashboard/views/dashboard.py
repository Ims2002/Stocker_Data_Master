"""
dashboard/views/dashboard.py — "Dashboard": página de entrada del v1
(2026-08-27, ver CONTEXTO.md "Roadmap v1 (MVP para publicar)"). Nació
como boceto separado de "Resumen" (views/inicio.py, 2026-08-25), pero al
preparar el v1 se decidió que sustituye a "Resumen" como entrada — dos
páginas contando una historia parecida no aportaba para una primera
publicación. "Resumen" sigue existiendo como archivo (no se borra), solo
dejó de registrarse en Inicio.py.

Diseño acordado con el usuario antes de escribir código (2026-08-25):
- Barra lateral con filtros (sector, ticker, horizonte, meses de
  histórico) — texto e indicadores dinámicos según la selección.
- Al elegir un ticker, TODO el dashboard se recentra a esa acción (KPIs de
  cabecera, gráfico, noticias, texto de insights) — no solo la tabla de
  noticias. Con st.columns, que ya apila verticalmente en pantallas
  estrechas (comportamiento nativo de Streamlit, sin CSS a mano), para que
  se lea bien en móvil.
- Un horizonte a la vez con selector (día/semana/mes), igual que
  "Predicciones", no los tres en paralelo.

RECORTADO 2026-08-25 (feedback del usuario tras ver el primer boceto): se
quitó el medidor de sentimiento general del mercado (era la única pieza
que NO se recentraba en la acción elegida, rompía la idea de "todo gira
en torno a una acción") y la tarjeta/enlace de "¿Funciona de verdad?"
(quedaba redundante con esa página dedicada, ya accesible desde la
navegación superior). El gráfico sigue marcando aciertos/fallos del
backtest sobre el precio — solo se quitó el KPI agregado y el texto que
lo repetían.

RECORTADO OTRA VEZ 2026-08-27 (alcance v1): el enlace de pie de página a
"Noticias de la acción" se quitó porque esa página ya no está registrada
en la navegación del v1 (ver Inicio.py) — enlazar a una página no
registrada rompe `st.page_link`. Quedó solo el enlace a "Predicciones",
que a su vez se quitó del todo el 2026-08-30 (ver más abajo) — ya está
accesible desde la navegación superior, no hacía falta duplicarlo aquí.

CABECERA REHECHA 2026-08-28: se quitó el `st.title("Dashboard")` (era
redundante con la pestaña de navegación, que ya dice "Dashboard"). Se
probó primero con un `st.title("STOCKER")` encima del subtítulo, pero
también se quitó el mismo día (redundante con el wordmark "Stocker" que
ya está en la barra de navegación superior — `ui.render_logo()` — no
hacía falta repetirlo dentro del contenido de la página). La cabecera
final es solo el subtítulo dinámico: logo del ticker + nombre de la
empresa + `#TICKER`, en gris claro `#9CA3AF` (mismo tono que ya se usaba
para "Neutral" en el sentimiento). El subtítulo depende de la acción
elegida en la barra lateral, así que se pinta después de leerla, no
antes. El aviso de fecha de datos (demo congelada) sigue justo debajo.

Detalle completo de todas estas decisiones en CONTEXTO.md, "Dashboard
unificado (mockup)" y "Roadmap v1 (MVP para publicar)".

REINCORPORADO + REDISEÑO 2026-08-30 (ver CONTEXTO.md, "Medidor de
sentimiento de mercado: reincorporado" y "Rediseño de layout: sin scroll
en 1920x1080"): el medidor semicircular de sentimiento de mercado vuelve
— esta vez como una tarjeta más dentro de la fila de KPIs (no una sección
propia a todo lo ancho), precisamente para no repetir el motivo por el
que se quitó la primera vez (rompía "todo gira en torno a la acción
elegida"): al ir en la fila de KPIs junto a Predicción/Probabilidad/
Último cierre, se lee como "un dato de contexto de mercado más", no como
un bloque desconectado del resto de la página. Además, todo el layout se
comprimió a dos columnas (gráfico | noticias + lectura rápida) en vez de
apilar cada sección a todo lo ancho — el objetivo es que quepa en una
pantalla de 1920x1080 sin hacer scroll. `data_access.get_market_sentiment_gauge()`
ya existía desde 2026-08-25 (se dejó sin usar a propósito, ver su
docstring) — no hizo falta ningún dato ni consulta nueva, solo volver a
llamarla y dibujar el `go.Indicator`.

REVERTIDO 2026-08-30 (mismo día): se probó una segunda pasada sobre este
mismo rediseño (cajas de altura fija igualada, líneas de degradado azul
marino en navbar/sidebar, más padding recortado, semicírculo con título
integrado y aguja) — el usuario, tras verla, pidió volver a la versión
de arriba (la del primer rediseño de este mismo día) tal cual estaba.
Ese CSS y esos cambios de layout se deshicieron en `ui.py` y aquí. Lo que
SÍ se mantiene de esa segunda pasada: el enlace "Profundizar en
Predicciones →" se quitó del todo (no se revirtió) — ya está accesible
desde la navegación superior.

LIBRERÍA DEL SEMICÍRCULO 2026-08-30 (mismo día): tras comparar
`streamlit-echarts` (Apache ECharts) vs. `streamviz` (envoltorio ligero
sobre el mismo Plotly, sin mantenimiento desde 2023) vs. pulir el
`go.Indicator` existente, el usuario eligió `streamlit-echarts`. El
semicírculo ahora se dibuja con `st_echarts()` en vez de
`go.Figure(go.Indicator(...))` — mismas bandas de color y aguja azul
`#1D4ED8` de antes, pero con degradado/animación propios de ECharts. Ver
CONTEXTO.md, "Semicírculo de sentimiento: librería nueva
(`streamlit-echarts`, 2026-08-30)".

ENLACE A "NOTICIAS DE LA ACCIÓN" 2026-09-08 (ver CONTEXTO.md, "Quitar
'¿Funciona de verdad?' y profundizar en noticias desde el Dashboard"):
la caja de "Últimas noticias de {ticker}" solo enseña 4 titulares
recortados (limit=4, ver `_recent_articles()` más abajo) — a propósito,
es una cabecera, no el lugar para profundizar. Debajo se añade un botón
"Ver todas las noticias →" que guarda el ticker elegido en
`st.session_state["noticias_ticker"]` y navega con `st.switch_page()` a
"Noticias de la acción" (views/sentimiento_por_accion.py, reincorporada
a la navegación el mismo día) — esa página lee y consume esa clave para
preseleccionar el mismo ticker, en vez de caer al ticker por defecto
(NVDA). No se usó `st.page_link` (el patrón ya existente en el resto del
dashboard para enlazar entre páginas) porque `st.page_link` no ejecuta
código Python al pulsarse — no hay forma de pasarle el ticker elegido;
`st.button` + `st.switch_page()` sí permite fijar el session_state justo
antes de cambiar de página.

LECTURA RÁPIDA A TODO EL ANCHO 2026-09-08 (mismo día, ver CONTEXTO.md
"Quitar '¿Funciona de verdad?' y profundizar en noticias desde el
Dashboard"): al añadir el botón "Ver todas las noticias →" dentro de
`col_side`, esa columna estrecha (noticias + botón + antes también
"Lectura rápida", los tres apilados) quedó más alta que `col_chart`
—reintrodujo el scroll vertical en 1920x1080 que el rediseño del
2026-08-30 ("Rediseño de layout: sin scroll en 1920x1080") había
eliminado a propósito. Fix: "Lectura rápida" sale de `col_side` y pasa a
ser su propia fila a todo el ancho, debajo de las dos columnas
(gráfico | noticias), en vez de apilada dentro de la columna estrecha —
reparte mejor el alto total de la página y vuelve a caber sin scroll.

Vive en views/, no pages/ — ver la nota en views/inicio.py sobre por qué.
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_echarts import st_echarts

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_access as da  # noqa: E402

engine = da.get_engine()

HORIZON_LABELS = {1: "Día (mañana)", 5: "Semana (~5 sesiones)", 20: "Mes (~20 sesiones)"}
_LABEL_TO_HORIZON = {v: k for k, v in HORIZON_LABELS.items()}

_SENTIMENT_COLOR = {
    "Bearish": "#d62728", "Somewhat-Bearish": "#e79a97",
    "Neutral": "#9CA3AF",
    "Somewhat-Bullish": "#86c98a", "Bullish": "#2ca02c",
}


@st.cache_data(ttl=300)
def _tickers():
    return da.list_tickers(engine)


@st.cache_data(ttl=300)
def _tickers_by_sector():
    return da.get_tickers_by_sector(engine)


@st.cache_resource
def _model_bundle(horizon: int):
    return da.load_latest_model(horizon=horizon)


@st.cache_data(ttl=300)
def _price_history(ticker: str, months: int):
    return da.get_price_history(engine, ticker, months=months)


@st.cache_data(ttl=300)
def _gold_for_ticker(ticker: str, months: int):
    return da.get_gold_train_for_ticker(engine, ticker, months=months)


@st.cache_data(ttl=300)
def _recent_articles(ticker: str):
    # limit=4 (antes 6): la columna de noticias ahora es más estrecha
    # (layout de dos columnas, 2026-08-30), menos artículos leen mejor
    # sin scroll interno.
    return da.get_recent_articles(engine, ticker, limit=4, min_relevance=0.5)


@st.cache_data(ttl=300)
def _market_sentiment_gauge():
    return da.get_market_sentiment_gauge(engine)


@st.cache_data(ttl=300)
def _daily_sentiment(ticker: str):
    return da.get_daily_sentiment_for_ticker(engine, ticker, months=1)


tickers = _tickers()
if not tickers:
    st.error("No hay tickers con datos en daily_prices. Ejecuta antes download.py → load.py.")
    st.stop()

meta_tickers = _tickers_by_sector()
sectores = ["Todos"] + sorted(meta_tickers["sector"].dropna().unique().tolist())

with st.sidebar:
    st.subheader("Filtros")
    sector = st.selectbox("Sector", sectores, index=0)
    tickers_filtrados = (
        tickers if sector == "Todos"
        else [t for t in tickers if t in set(meta_tickers.loc[meta_tickers["sector"] == sector, "ticker"])]
    ) or tickers
    ticker = st.selectbox("Acción", tickers_filtrados, index=da.default_ticker_index(tickers_filtrados))
    horizonte_label = st.segmented_control(
        "Horizonte de predicción", list(HORIZON_LABELS.values()), default=HORIZON_LABELS[1],
    )
    meses = st.slider("Meses de histórico", min_value=1, max_value=24, value=6)
    st.caption(
        f"Mostrando **{len(tickers_filtrados)}** acción(es)"
        + (f" del sector **{sector}**" if sector != "Todos" else " de todo el universo")
        + " — todo el dashboard de abajo se recentra a la acción elegida."
    )

horizon = _LABEL_TO_HORIZON[horizonte_label] if horizonte_label else 1
target_col = "target_up_down" if horizon == 1 else f"target_up_down_{horizon}d"

meta = da.get_ticker_metadata(engine, ticker)
logo_url = da.ticker_logo_url(ticker)

_logo_html = (
    f'<img src="{logo_url}" width="26" height="26" '
    'style="border-radius:6px; vertical-align:middle; margin-right:8px;">'
) if logo_url else ""
_nombre_empresa = html.escape(meta.get("nombre") or ticker)
st.markdown(
    f'<div style="margin-top:4px;">{_logo_html}'
    f'<span style="color:#9CA3AF; font-size:1.05rem; vertical-align:middle;">'
    f'{_nombre_empresa} · <strong>#{ticker}</strong></span></div>',
    unsafe_allow_html=True,
)
st.caption(f"{meta.get('sector') or 'sector desconocido'} · {meta.get('pais') or 'país desconocido'}")

try:
    bundle = _model_bundle(horizon)
except FileNotFoundError:
    bundle = None

if bundle is None:
    st.info(
        f"Todavía no hay ningún modelo entrenado para el horizonte **{horizonte_label}** — ejecuta "
        f"`python src/model.py --horizon {horizon}` (y `python src/predict.py --horizon {horizon}`). "
        "Elige **Día (mañana)** mientras tanto.",
        icon="🚧",
    )
    st.stop()

prices = _price_history(ticker, meses)
if prices.empty:
    st.warning(f"No hay histórico de precios para {ticker} en la ventana seleccionada.")
    st.stop()

gold = _gold_for_ticker(ticker, meses)
n_sin_target = 0
if not gold.empty and horizon != 1:
    n_sin_target = int(gold[target_col].isna().sum())
    gold = gold[gold[target_col].notna()].copy()

latest_pred = da.get_latest_prediction(engine, ticker, horizon=horizon)

last_close = float(prices["close"].iloc[-1])
prev_close = float(prices["close"].iloc[-2]) if len(prices) >= 2 else None
var_pct = (last_close / prev_close - 1) if prev_close else None

# --- KPIs de cabecera (recentrados en la acción elegida) + sentimiento
# de mercado (agregado, no depende del ticker — va aquí, como una
# tarjeta más de contexto, en vez de una sección propia a todo lo ancho,
# ver docstring del módulo) ---
with st.container(border=True):
    col1, col2, col3, col4 = st.columns([1, 1, 1, 1.3])
    if latest_pred and latest_pred["predicted_target_up_down"] is not None:
        direccion = "📈 Sube" if latest_pred["predicted_target_up_down"] == 1 else "📉 Baja"
        col1.metric(f"Predicción · {latest_pred['date_predicha']}", direccion)
        # Bug corregido 2026-09-09 (ver CONTEXTO.md "Probabilidad
        # mostrada para la dirección equivocada"): `predicted_probability`
        # guarda SIEMPRE P(sube), no la probabilidad de la dirección
        # predicha — `da.prediction_confidence()` ya lo ajusta.
        # (2026-09-10: se probó además marcar como "poco concluyente" la
        # dirección cuando la confianza cae por debajo del 55%, ver
        # CONTEXTO.md "Feedback de un tutor: comunicación de predicciones
        # poco concluyentes..." — el usuario pidió revertirlo, así que
        # "Sube"/"Baja" se muestra siempre, sin ese aviso.)
        confianza = da.prediction_confidence(
            latest_pred["predicted_target_up_down"], latest_pred["predicted_probability"]
        )
        col2.metric(
            "Probabilidad estimada", f"{confianza:.1%}",
            help=(
                "Probabilidad que el modelo asigna a ESTA dirección concreta. Salida cruda de Random "
                "Forest, sin garantía de estar calibrada como confianza real (ver calibración en "
                "'¿Funciona de verdad?')."
            ),
        )
    else:
        col1.metric("Predicción", "—")
        col2.metric("Probabilidad estimada", "—")
    col3.metric(
        "Último cierre",
        f"${last_close:,.2f}",
        delta=f"{var_pct:+.2%}" if var_pct is not None else None,
    )
    with col4:
        market_gauge = _market_sentiment_gauge()
        if market_gauge:
            valor = round(float(market_gauge["sentimiento"]), 2)
            gauge_option = {
                "series": [{
                    "type": "gauge",
                    "startAngle": 180,
                    "endAngle": 0,
                    "min": -1,
                    "max": 1,
                    "radius": "100%",
                    "center": ["50%", "80%"],
                    "progress": {"show": False},
                    "splitNumber": 4,
                    "axisLine": {
                        "lineStyle": {
                            "width": 10,
                            "color": [
                                [0.325, "#f6cdc9"],
                                [0.425, "#fbe4e1"],
                                [0.575, "#EEF0F2"],
                                [0.675, "#ddeedd"],
                                [1, "#c3e6c3"],
                            ],
                        }
                    },
                    "pointer": {
                        "length": "58%",
                        "width": 4,
                        "itemStyle": {"color": "#1D4ED8"},
                    },
                    "anchor": {
                        "show": True,
                        "showAbove": True,
                        "size": 7,
                        "itemStyle": {"color": "#1D4ED8", "borderColor": "#1D4ED8", "borderWidth": 1},
                    },
                    "axisTick": {"show": False},
                    "splitLine": {
                        "length": 7,
                        "distance": -10,
                        "lineStyle": {"color": "#9CA3AF", "width": 1},
                    },
                    "axisLabel": {"color": "#9CA3AF", "fontSize": 8, "distance": -18},
                    "title": {"show": False},
                    "detail": {
                        "valueAnimation": True,
                        "fontSize": 18,
                        "fontWeight": 600,
                        "color": "#0B0F19",
                        "offsetCenter": [0, "-15%"],
                        "formatter": "{value}",
                    },
                    "data": [{"value": valor}],
                }]
            }
            st_echarts(options=gauge_option, height="110px")
            # Alcance/ventana explícitos en el propio texto (2026-09-10,
            # feedback de un tutor: dos cifras de nº de artículos en la
            # misma página sin explicar por qué difieren — ver
            # CONTEXTO.md "Feedback de un tutor: comunicación de
            # predicciones poco concluyentes, trazabilidad y consistencia
            # del sentimiento"). Este número es de TODO el universo
            # (208 tickers) en los últimos `n_dias` días naturales — muy
            # distinto del de "Lectura rápida" más abajo, que es de UN
            # solo ticker en el último mes. Antes ninguno de los dos
            # decía su alcance, así que parecían inconsistentes entre sí
            # sin serlo (miden cosas distintas a propósito).
            st.caption(
                f"Sentimiento de mercado (todo el universo, últimos {market_gauge['n_dias']} días): "
                f"**{market_gauge['label']}** ({market_gauge['n_articles']} art.)"
            )
        else:
            st.caption("Sentimiento de mercado: sin datos todavía.")

# --- Fila principal: gráfico (izquierda) + noticias/lectura rápida (derecha) ---
col_chart, col_side = st.columns([2, 1])

with col_chart:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=prices["date"], y=prices["close"], mode="lines", name="Cierre real",
            line=dict(color="#1D4ED8", width=1.5),
        )
    )

    test_date_min = bundle.get("test_date_min")
    gold_eval = pd.DataFrame()
    if not gold.empty:
        gold = gold.copy()
        gold["pred"] = da.predict_for_gold_rows(bundle, gold)
        gold["acierto"] = gold["pred"] == gold[target_col]

        if test_date_min:
            test_cutoff = pd.Timestamp(test_date_min)
            fig.add_vline(
                x=test_cutoff, line_dash="dot", line_color="gray",
                annotation_text="examen real →", annotation_position="top",
            )
            gold_eval = gold[gold["date"] >= test_cutoff]

        aciertos = gold[gold["acierto"]]
        fallos = gold[~gold["acierto"]]
        fig.add_trace(go.Scatter(
            x=aciertos["date"], y=aciertos["close"], mode="markers", name="Acertó",
            marker=dict(color="#2ca02c", size=6, symbol="circle"),
        ))
        fig.add_trace(go.Scatter(
            x=fallos["date"], y=fallos["close"], mode="markers", name="Falló",
            marker=dict(color="#d62728", size=6, symbol="x"),
        ))

    if latest_pred and latest_pred["predicted_target_up_down"] is not None:
        direccion = "SUBE" if latest_pred["predicted_target_up_down"] == 1 else "BAJA"
        color = "#2ca02c" if latest_pred["predicted_target_up_down"] == 1 else "#d62728"
        fig.add_trace(go.Scatter(
            x=[pd.Timestamp(latest_pred["date_predicha"])], y=[last_close], mode="markers+text",
            name=f"Predicción {latest_pred['date_predicha']}",
            marker=dict(color=color, size=14, symbol="star"), text=[direccion], textposition="top center",
        ))

    fig.update_layout(
        height=300, margin=dict(l=10, r=10, t=25, b=10),
        yaxis_title="Precio de cierre (USD)", xaxis_title=None,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=10)),
        plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
    )
    with st.container(border=True):
        st.plotly_chart(fig, width="stretch")
    if n_sin_target:
        st.caption(
            f"ℹ️ Las últimas {n_sin_target} sesiones todavía no tienen las {horizon} sesiones futuras "
            "necesarias para saber si acertaron."
        )

with col_side:
    st.markdown(f"**Últimas noticias de {ticker}**")
    articulos = _recent_articles(ticker)
    with st.container(border=True, height=200):
        if articulos.empty:
            st.caption(f"🕒 Todavía no hay noticias guardadas de {ticker} (relevancia ≥ 0.5).")
        else:
            items_html = []
            for _, row in articulos.iterrows():
                label = row["ticker_sentiment_label"]
                color = _SENTIMENT_COLOR.get(label, "#9CA3AF")
                label_es = da.SENTIMENT_LABEL_ES.get(label, label)
                titulo = html.escape(str(row["title"]))
                if len(titulo) > 60:
                    titulo = titulo[:58] + "…"
                fuente = html.escape(str(row["source"] or "fuente desconocida"))
                items_html.append(
                    f"""<div style="padding:5px 0; border-bottom:1px solid #EEF0F2;">
                        <div style="font-size:12.5px; color:#0B0F19; line-height:1.3;">{titulo}</div>
                        <div style="font-size:10.5px; color:#9CA3AF; margin-top:2px;">
                            {fuente} · {row['date']} ·
                            <span style="background:{color}22; color:{color}; padding:1px 7px; border-radius:999px; font-weight:500;">{html.escape(label_es)}</span>
                        </div>
                    </div>"""
                )
            st.markdown("".join(items_html), unsafe_allow_html=True)

    if st.button("Ver todas las noticias →", width="stretch"):
        st.session_state["noticias_ticker"] = ticker
        st.switch_page("views/sentimiento_por_accion.py")

# --- Lectura rápida: a todo el ancho, debajo de las dos columnas (no
# dentro de col_side) — ver docstring del módulo, "Lectura rápida a todo
# el ancho". Al meter el botón "Ver todas las noticias →" en col_side,
# esa columna quedó más alta que col_chart y reintrodujo scroll vertical
# en 1920x1080; sacar este bloque a una fila propia de ancho completo
# reparte mejor el alto total de la página.
st.markdown("**Lectura rápida**")
with st.container(border=True):
    partes = []
    if latest_pred and latest_pred["predicted_target_up_down"] is not None:
        direccion_txt = "**subirá**" if latest_pred["predicted_target_up_down"] == 1 else "**bajará**"
        # Mismo fix que en el KPI de arriba: confianza en la dirección
        # predicha, no P(sube) en crudo. (2026-09-10: el aviso de "poco
        # concluyente" que hubo aquí se revirtió a petición del usuario —
        # ver CONTEXTO.md.)
        confianza_txt = da.prediction_confidence(
            latest_pred["predicted_target_up_down"], latest_pred["predicted_probability"]
        )
        partes.append(
            f"El modelo predice que **{ticker}** {direccion_txt} en el horizonte "
            f"**{horizonte_label.lower()}**, con una probabilidad estimada del "
            f"**{confianza_txt:.0%}**."
        )
    else:
        partes.append(f"Todavía no hay una predicción guardada para **{ticker}** en este horizonte.")

    daily_sent = _daily_sentiment(ticker)
    if not daily_sent.empty:
        n_art = int(daily_sent["n_articles"].sum())
        tono = float((daily_sent["avg_sentiment_score"] * daily_sent["n_articles"]).sum() / n_art) if n_art else 0.0
        tono_label = da.sentiment_scalar_label(tono)
        # "de SOLO esta acción" explícito (2026-09-10, ver comentario del
        # caption de "Sentimiento de mercado" más arriba) — para que no
        # se lea como comparable al nº de artículos de todo el universo.
        partes.append(
            f"Las noticias de **solo {ticker}** en el último mes tienen un tono **{tono_label.lower()}** "
            f"({n_art} artículos de {ticker}, no de todo el mercado)."
        )
    else:
        partes.append(f"Sin noticias recientes de {ticker} en el último mes.")

    st.markdown(" ".join(partes))
    st.caption(
        "Texto generado a partir de los mismos datos de arriba — el sentimiento de noticias es "
        "informativo, sin evidencia de que ayude a predecir el precio (ver CONTEXTO.md)."
    )

st.divider()
# Pie de trazabilidad del pipeline (2026-09-10, feedback de un tutor:
# "falta demostrar que las cifras y funcionalidades que muestra pueden
# salir realmente del pipeline" — ver CONTEXTO.md "Feedback de un tutor:
# comunicación de predicciones poco concluyentes, trazabilidad y
# consistencia del sentimiento"). Barato de mantener honesto: no dice
# nada que no sea ya cierto en el resto del proyecto, solo lo hace
# visible en la propia página en vez de asumir que quien la mira ya
# conoce el repositorio.
st.caption(
    "Todas las cifras de esta página salen de `stocker.db`, generado por el pipeline real "
    "(`download.py → load.py → gold.py → model.py → predict.py`, ver `docs/entregas/` para el detalle "
    "metodológico) — el dashboard nunca escribe ni inventa datos."
)
