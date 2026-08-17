"""
dashboard/views/inicio.py — "Resumen": primera versión de un dashboard
unificado (ver CONTEXTO.md, "Resumen: dashboard unificado", 2026-08-13).
Reúne los KPIs y gráficos más importantes de cada pestaña de detalle en
un único vistazo, para no tener que entrar en cada una para saber "cómo
va todo". Es un v1 explícitamente pensado para irse puliendo con el
tiempo, no la versión final — cada pestaña de detalle se mantiene intacta
para quien quiera profundizar.

NOTA: vive en views/, no en pages/. Streamlit trata cualquier carpeta
llamada "pages" junto al entrypoint como app multipágina "clásica" (con
navegación automática por sidebar) incluso cuando el entrypoint usa
st.navigation() explícitamente — y al intentar registrar ambas cosas a la
vez, colisiona (el propio Inicio.py se auto-registra como página con la
ruta "Inicio", igual que este archivo, y Streamlit lo rechaza por ruta
duplicada). Usar un nombre de carpeta distinto evita ese comportamiento
heredado por completo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data_access as da  # noqa: E402

st.title("Resumen")
st.caption(
    "Predicción de dirección (sube/baja) del cierre del día siguiente para un universo de acciones de EE. UU. "
    "— proyecto de máster orientado a producción real. Primera versión de un vistazo unificado: reúne lo más "
    "importante de cada pestaña; para el detalle completo, entra en la pestaña correspondiente. "
    "Metodología completa en CONTEXTO.md."
)

engine = da.get_engine()


@st.cache_data(ttl=300)
def _overview():
    from sqlalchemy import func, select

    import db as dbmod

    tickers = da.list_tickers(engine)
    with engine.begin() as conn:
        max_date = conn.execute(select(func.max(dbmod.daily_prices.c.date))).scalar()
        n_predictions = conn.execute(select(func.count()).select_from(dbmod.predictions)).scalar()
        n_news = conn.execute(select(func.count()).select_from(dbmod.news_articles)).scalar()
    return {
        "n_tickers": len(tickers),
        "max_date": max_date,
        "n_predictions": n_predictions,
        "n_news": n_news,
    }


@st.cache_resource
def _model_bundle():
    return da.load_latest_model()


@st.cache_data(ttl=300)
def _resolved():
    return da.get_resolved_predictions(engine)


@st.cache_data(ttl=300)
def _latest_summary():
    return da.get_latest_predictions_summary(engine)


@st.cache_data(ttl=300)
def _market_daily():
    return da.get_market_sentiment_daily(engine)


@st.cache_data(ttl=300)
def _breadth_history():
    return da.get_market_breadth_history(engine, horizon=1, days=90)


@st.cache_data(ttl=300)
def _horizon_comparison():
    return da.get_horizon_comparison(engine)


@st.cache_data(ttl=300)
def _volatility_accuracy(_model_version: str):
    # _model_version solo participa en la clave de caché (para invalidar
    # si cambia el modelo cargado) — la función usa el bundle vía closure.
    return da.get_accuracy_by_volatility(engine, bundle)


@st.cache_data(ttl=300)
def _confidence_accuracy(_model_version: str, threshold: float):
    return da.get_confidence_accuracy(engine, bundle, threshold=threshold)


try:
    overview = _overview()
except Exception as exc:  # noqa: BLE001
    st.error(
        "No se ha podido leer la base de datos (`data/stocker.db`). ¿Ya ejecutaste el pipeline "
        f"(download.py → load.py → gold.py → model.py → predict.py)? Detalle: {exc}"
    )
    st.stop()

with st.container(border=True):
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Tickers con datos", overview["n_tickers"])
    col2.metric("Último día cargado", str(overview["max_date"]) if overview["max_date"] else "—")
    col3.metric("Predicciones guardadas", overview["n_predictions"])
    col4.metric("Artículos de noticias", overview["n_news"])

st.divider()

# --- Predicciones de hoy: resumen agregado (nuevo, solo en Resumen) ---
st.subheader("Predicciones más recientes")
latest = _latest_summary()
if latest is None:
    st.info(
        "Todavía no hay ninguna predicción guardada — ejecuta `python src/predict.py` (o el pipeline "
        "completo, `run_daily_pipeline.bat`).",
        icon="🕒",
    )
else:
    with st.container(border=True):
        st.caption(f"Última fecha con predicciones: **{latest['date_predicha']}** ({latest['n_total']} tickers)")
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Predice \"sube\"", f"{latest['n_up']} ({latest['n_up'] / latest['n_total']:.0%})")
        col_b.metric("Predice \"baja\"", f"{latest['n_down']} ({latest['n_down'] / latest['n_total']:.0%})")
        col_c.metric("Probabilidad media", f"{latest['avg_probability']:.0%}")
    st.caption("Detalle por acción en la pestaña **Predicciones**.")

st.divider()

# --- Amplitud de mercado: evolución histórica del % que predijo "sube" ---
st.subheader("Amplitud de mercado (histórico)")
breadth = _breadth_history()
if breadth.empty:
    st.info(
        "Todavía no hay suficiente histórico de predicciones a horizonte día para trazar esta evolución.",
        icon="🕒",
    )
else:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=breadth["date_predicha"], y=breadth["pct_up"], mode="lines+markers", name="% predijo \"sube\"",
            line=dict(color="#1D4ED8", width=2), marker=dict(size=5), fill="tozeroy",
            fillcolor="rgba(29, 78, 216, 0.08)",
        )
    )
    fig.add_hline(y=0.5, line_dash="dot", line_color="#9CA3AF")
    fig.update_layout(
        height=220, margin=dict(l=10, r=10, t=10, b=10),
        yaxis=dict(range=[0, 1], tickformat=".0%", title=None), xaxis_title=None,
        plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
    )
    with st.container(border=True):
        st.plotly_chart(fig, width="stretch")
    st.caption(
        "Qué fracción del universo predijo el modelo que subiría cada sesión (horizonte día, últimos 90 "
        "días) — es la inclinación del propio modelo sobre el mercado, no una medida de acierto: se puede "
        "combinar entre versiones de modelo sin el problema de mezclar `model_version` que sí afecta al "
        "acierto real (ver aviso en **Día a día**)."
    )

st.divider()

# --- Rendimiento del modelo: modelo vs. baselines (backtest oficial) ---
st.subheader("¿Funciona de verdad? (backtest)")
try:
    bundle = _model_bundle()
    has_metrics = "metrics_model" in bundle
except FileNotFoundError:
    bundle = None
    has_metrics = False

if not has_metrics:
    st.info(
        "No hay métricas de backtest disponibles todavía — entrena un modelo con `python src/model.py`.",
        icon="🕒",
    )
else:
    m_model = bundle["metrics_model"]
    m_majority = bundle["metrics_majority_baseline"]
    m_persistence = bundle["metrics_persistence_baseline"]
    beats_both = m_model["accuracy"] > m_majority["accuracy"] and m_model["accuracy"] > m_persistence["accuracy"]

    col_a, col_b = st.columns([1, 2])
    with col_a:
        with st.container(border=True):
            st.metric("Accuracy del modelo (test)", f"{m_model['accuracy']:.1%}")
            st.metric("Predecir siempre lo más frecuente", f"{m_majority['accuracy']:.1%}")
            st.metric("Predecir que mañana repite a hoy", f"{m_persistence['accuracy']:.1%}")
    with col_b:
        if beats_both:
            st.success("El modelo le gana a las dos formas simples de adivinar.", icon="✅")
        else:
            st.warning(
                "El modelo NO le gana de forma clara a esas dos formas simples de adivinar — resultado "
                "honesto, esperable a corto plazo en bolsa (más detalle en CONTEXTO.md y en "
                "**¿Funciona de verdad?**).",
                icon="⚠️",
            )
        st.caption(
            f"Modelo `{bundle.get('_model_version', '?')}`, examinado del {bundle.get('test_date_min')} "
            f"al {bundle.get('test_date_max')}."
        )

st.divider()

# --- Comparativa por horizonte: día vs. semana vs. mes ---
st.subheader("Comparativa por horizonte")
horizonte_cmp = _horizon_comparison()
if horizonte_cmp.empty:
    st.info("No hay ningún modelo entrenado todavía en ningún horizonte.", icon="🕒")
else:
    filas = []
    for _, r in horizonte_cmp.iterrows():
        if r["disponible"]:
            filas.append({
                "Horizonte": r["etiqueta"].capitalize(),
                "Accuracy modelo": f"{r['accuracy_modelo']:.1%}",
                "Baseline mayoritario": f"{r['accuracy_mayoritario']:.1%}",
                "Baseline persistencia": f"{r['accuracy_persistencia']:.1%}",
                "¿Le gana a ambos?": "✅" if (
                    r["accuracy_modelo"] > r["accuracy_mayoritario"] and r["accuracy_modelo"] > r["accuracy_persistencia"]
                ) else "—",
            })
        else:
            filas.append({
                "Horizonte": r["etiqueta"].capitalize(), "Accuracy modelo": "sin entrenar",
                "Baseline mayoritario": "—", "Baseline persistencia": "—", "¿Le gana a ambos?": "—",
            })
    with st.container(border=True):
        st.dataframe(pd.DataFrame(filas), width="stretch", hide_index=True)
    pendientes = horizonte_cmp.loc[~horizonte_cmp["disponible"], "etiqueta"].tolist()
    if pendientes:
        st.caption(
            f"Horizonte(s) sin modelo entrenado todavía: {', '.join(pendientes)} — "
            f"`python src/model.py --horizon N` para completarlos (ver CONTEXTO.md)."
        )
    st.caption("Selecciona el horizonte en la pestaña **Predicciones** para ver el detalle por acción.")

st.divider()

# --- Día a día: acierto real en producción, versión más reciente ---
st.subheader("¿Sigue funcionando en producción? (día a día)")
resolved = _resolved()
if resolved.empty:
    st.info(
        "Todavía no hay ninguna predicción resuelta contra el cierre real — vuelve en unos días.",
        icon="🕒",
    )
else:
    version_mas_reciente = resolved.sort_values("date_predicha")["model_version"].iloc[-1]
    sub = resolved[resolved["model_version"] == version_mas_reciente]
    por_dia = sub.groupby("date_predicha")["acierto"].mean().reset_index()

    col_a, col_b = st.columns([1, 2])
    with col_a:
        with st.container(border=True):
            st.metric("Acierto real (versión actual)", f"{sub['acierto'].mean():.1%}")
            st.caption(f"Modelo `{version_mas_reciente}` · {sub['date_predicha'].nunique()} sesiones de mercado")
    with col_b:
        fig = go.Figure()
        fig.add_trace(go.Bar(x=por_dia["date_predicha"], y=por_dia["acierto"], marker_color="#1D4ED8"))
        fig.add_hline(y=0.5, line_dash="dot", line_color="#9CA3AF")
        fig.update_layout(
            height=220, margin=dict(l=10, r=10, t=10, b=10),
            yaxis=dict(range=[0, 1], tickformat=".0%"), xaxis_title=None,
            plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
        )
        st.plotly_chart(fig, width="stretch")
    st.caption(
        "⚠️ Solo la versión de modelo más reciente, nunca mezclada con versiones antiguas (ver CONTEXTO.md). "
        "Detalle completo en **Día a día**."
    )

st.divider()

# --- En qué se fija el modelo: top 5 features ---
st.subheader("En qué se fija el modelo")
if bundle is not None:
    fi = da.feature_importances(bundle)
    if fi is not None:
        top5 = fi.head(5).sort_values("importancia")
        fig = px.bar(
            top5, x="importancia", y="etiqueta", orientation="h",
            color_discrete_sequence=["#1D4ED8"],
        )
        fig.update_layout(
            height=280, margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title="Importancia relativa", yaxis_title=None,
            plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
        )
        st.plotly_chart(fig, width="stretch")
        st.caption("Top 5 de todas las features. Detalle completo en **En qué se fija**.")
    else:
        st.info("El modelo cargado no expone importancia de features.", icon="ℹ️")
else:
    st.info("No hay ningún modelo entrenado todavía.", icon="🕒")

st.divider()

# --- ¿Cuándo acierta más el modelo?: régimen de volatilidad + confianza ---
st.subheader("¿Cuándo acierta más el modelo?")
if bundle is None:
    st.info("No hay ningún modelo entrenado todavía.", icon="🕒")
else:
    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("**Por régimen de volatilidad**")
        vol_acc = _volatility_accuracy(bundle.get("_model_version", ""))
        if vol_acc.empty:
            st.caption("Sin datos suficientes para calcularlo.")
        else:
            fig = px.bar(
                vol_acc, x="bucket", y="acierto", text=vol_acc["acierto"].map(lambda v: f"{v:.1%}"),
                color_discrete_sequence=["#1D4ED8"],
            )
            fig.add_hline(y=0.5, line_dash="dot", line_color="#9CA3AF")
            fig.update_traces(textposition="outside")
            fig.update_layout(
                height=240, margin=dict(l=10, r=10, t=10, b=10),
                yaxis=dict(range=[0, 1], tickformat=".0%", title=None), xaxis_title="Volatilidad reciente (terciles)",
                plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
            )
            st.plotly_chart(fig, width="stretch")
            st.caption("Acierto sobre el test oficial, agrupado en 3 tercios por `volatility_10d`.")

    with col_b:
        st.markdown("**Predicciones de alta confianza**")
        umbral = st.slider(
            "Umbral de confianza (probabilidad a favor de la clase predicha)",
            min_value=0.52, max_value=0.70, value=0.55, step=0.01,
        )
        conf = _confidence_accuracy(bundle.get("_model_version", ""), umbral)
        if conf is None:
            st.caption("Sin datos suficientes para calcularlo.")
        else:
            sub_a, sub_b = st.columns(2)
            sub_a.metric("Acierto — todas", f"{conf['acierto_total']:.1%}")
            if conf["acierto_alta"] is not None:
                sub_b.metric(
                    f"Acierto — alta confianza ({conf['n_alta']})",
                    f"{conf['acierto_alta']:.1%}",
                    delta=f"{(conf['acierto_alta'] - conf['acierto_total']) * 100:+.1f} pts",
                )
            else:
                sub_b.metric("Acierto — alta confianza", "sin datos", help="Ningún caso supera ese umbral.")
            st.caption(
                f"Solo el {conf['pct_alta']:.1%} de las predicciones del test supera este umbral — con "
                "Random Forest y una señal débil, la probabilidad rara vez se aleja mucho de 0.5."
            )

st.divider()

# --- Sentimiento del mercado: tendencia compacta ---
st.subheader("Sentimiento del mercado")
market = _market_daily()
if market.empty:
    st.info("Todavía no hay noticias guardadas de ningún ticker. Vuelve en unos días.", icon="🕒")
else:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Bar(x=market["date"], y=market["n_articles"], name="Nº de artículos", marker_color="#EEF0F2"),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=market["date"], y=market["sentimiento_medio"], name="Tono medio", mode="lines",
            line=dict(color="#1D4ED8", width=2),
        ),
        secondary_y=True,
    )
    fig.add_hline(y=0, line_dash="dot", line_color="#9CA3AF", secondary_y=True)
    fig.update_yaxes(secondary_y=False, showgrid=False, title_text=None)
    fig.update_yaxes(secondary_y=True, range=[-1, 1], title_text=None)
    fig.update_layout(
        height=260, margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
    )
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "Informativo, no predictivo (ver CONTEXTO.md, \"¿Ayuda el sentimiento a acertar más?\"). "
        "Detalle completo en **Sentimiento del mercado** y **Noticias de la acción**."
    )

st.divider()

st.subheader("Cómo leer este dashboard")
st.markdown(
    """
Esta página es un resumen — cada sección tiene su propia pestaña con más detalle:

- **Predicciones** — elige una acción y compara su historial real con lo que el modelo habría dicho cada
  día, más la predicción para la próxima sesión.
- **En qué se fija** — importancia de features completa.
- **¿Funciona de verdad?** — el modelo contra los baselines, con la explicación completa del contrato de
  evaluación.
- **Día a día** — acierto real en producción, todas las versiones de modelo, sin mezclar.
- **Noticias de la acción** / **Sentimiento del mercado** — informativo, sin evidencia de que ayude a
  predecir la dirección del precio (ver CONTEXTO.md).
    """
)

st.info(
    "El horizonte de predicción es actualmente **solo a 1 día** (la sesión de mercado siguiente).",
    icon="ℹ️",
)
