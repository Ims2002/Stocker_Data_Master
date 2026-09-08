"""
dashboard/premium_chat.py — chat experto de la página "Contenido Premium"
(2026-08-31, ver CONTEXTO.md "Chat experto sobre earnings: viabilidad y
diseño"). Separado de `views/premium.py` a propósito, para que esa vista
se quede en layout puro — aquí vive todo lo relacionado con construir el
contexto, llamar a la API de Anthropic y limitar el uso.

DISEÑO DELIBERADAMENTE SIN "RAG de verdad" (sin embeddings ni base de
datos vectorial): el corpus por ticker (comunicado de resultados +
transcripción de la earnings call + el análisis ya redactado) cabe
entero en el contexto de Claude sin necesitar recuperación selectiva —
trocear documentos tan cortos suele empeorar la calidad de las
respuestas, no mejorarla. Si en el futuro el corpus crece mucho (muchos
trimestres × muchos tickers), ahí sí compensaría montar recuperación
vectorial de verdad; de momento sería ingeniería de sobra.

QUÉ ENTRA EN EL CORPUS Y QUÉ NO (importante, ver CONTEXTO.md): los
documentos oficiales de la compañía (comunicado de resultados,
transcripción de la earnings call — de dominio público, publicados por
la propia empresa para este uso) y el análisis que redacta este mismo
asistente a partir de ellos. NUNCA la transcripción del vídeo de
terceros que sirvió de guía para escribir el análisis — guardar y servir
eso dentro de un producto de pago sería reproducir contenido con
derechos de autor de otra persona, no transformarlo (mismo motivo por el
que ya se excluye del propio `<TICKER>.md`, ver
`feedback_strip_creator_refs_premium_content` en memoria).

COSTE Y LÍMITES: cada pregunta llama a la API de Anthropic (dinero real,
a diferencia del resto del dashboard). La página de Contenido Premium es
pública, sin sistema de login todavía, así que hay dos límites: uno por
sesión de navegador (`st.session_state`, se reinicia si el usuario
recarga la página) y uno global por día, en un fichero local aparte
(`_USAGE_FILE`) — ÚNICA excepción documentada a la regla de "el
dashboard nunca escribe nada" (ver dashboard/README.md), y solo para
este fichero de conteo de uso, nunca para `stocker.db` ni para
`models/`.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from config import (  # noqa: E402
    ANTHROPIC_API_KEY,
    ANTHROPIC_WORKSPACE_ID,
    PREMIUM_CHAT_MAX_PER_DAY_GLOBAL,
    PREMIUM_CHAT_MAX_PER_SESSION,
    PREMIUM_CHAT_MODEL,
)

_PREMIUM_DIR = Path(__file__).resolve().parent.parent / "docs" / "premium"
_USAGE_FILE = Path(__file__).resolve().parent / ".premium_chat_usage.json"

SYSTEM_PROMPT_TEMPLATE = """Eres un asistente experto que responde preguntas sobre los resultados \
financieros trimestrales de {ticker} ({nombre}), usando EXCLUSIVAMENTE la información que se te \
proporciona a continuación (el comunicado oficial de resultados, la transcripción de la llamada \
con analistas, y un análisis ya redactado sobre ese trimestre).

Reglas que debes seguir siempre:
- Responde solo con lo que aparece en los documentos proporcionados. Si la pregunta no se puede \
responder con esa información, dilo con claridad en vez de inventar datos o usar conocimiento \
externo sobre la empresa.
- No des recomendaciones de inversión personalizadas (comprar, vender, mantener, precio objetivo, \
si es "buen momento" para invertir, etc.). Si te preguntan eso, explica que no puedes dar ese tipo \
de consejo y ofrece en su lugar los datos objetivos relevantes de los documentos para que la \
persona se forme su propia opinión.
- Explica los conceptos financieros o contables en lenguaje sencillo cuando la pregunta lo \
requiera — quien pregunta puede no tener formación en inversión.
- Responde en español, de forma directa y sin relleno innecesario.

--- DOCUMENTOS DE {ticker} ---

{corpus}
"""


def _ticker_dir(ticker: str) -> Path:
    return _PREMIUM_DIR / "hyperscalers"


def build_corpus(ticker: str) -> str | None:
    """Concatena el análisis redactado + los documentos oficiales
    disponibles de `ticker` en un único texto con secciones etiquetadas.
    None si todavía no hay ni siquiera el análisis (chat no disponible
    para ese ticker)."""
    base = _ticker_dir(ticker)
    analysis_path = base / f"{ticker}.md"
    if not analysis_path.exists():
        return None

    parts = [f"## Análisis redactado\n\n{analysis_path.read_text(encoding='utf-8')}"]

    fuentes_dir = base / f"{ticker}_fuentes"
    if fuentes_dir.exists():
        for fuente_path in sorted(fuentes_dir.glob("*.md")):
            parts.append(
                f"## Documento oficial: {fuente_path.stem}\n\n{fuente_path.read_text(encoding='utf-8')}"
            )
    return "\n\n---\n\n".join(parts)


def chat_available() -> bool:
    return bool(ANTHROPIC_API_KEY)


def max_per_session() -> int:
    return PREMIUM_CHAT_MAX_PER_SESSION


# --- Límites de uso --------------------------------------------------------

def _today_str() -> str:
    return dt.date.today().isoformat()


def _read_usage() -> dict:
    if not _USAGE_FILE.exists():
        return {"date": _today_str(), "count": 0}
    try:
        data = json.loads(_USAGE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"date": _today_str(), "count": 0}
    if data.get("date") != _today_str():
        return {"date": _today_str(), "count": 0}
    return data


def _write_usage(data: dict) -> None:
    try:
        _USAGE_FILE.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass  # el chat no debe romperse porque no se pudo escribir el contador


def global_usage_today() -> int:
    return _read_usage().get("count", 0)


def _register_global_usage() -> None:
    data = _read_usage()
    data["count"] = data.get("count", 0) + 1
    _write_usage(data)


def session_usage_key(ticker: str) -> str:
    return f"_premium_chat_count_{ticker}"


def session_usage(ticker: str) -> int:
    return st.session_state.get(session_usage_key(ticker), 0)


def limits_reached(ticker: str) -> str | None:
    """None si se puede preguntar; si no, el motivo en texto para mostrar
    al usuario."""
    if session_usage(ticker) >= PREMIUM_CHAT_MAX_PER_SESSION:
        return (
            f"Has alcanzado el límite de {PREMIUM_CHAT_MAX_PER_SESSION} preguntas por sesión para "
            f"{ticker}. Recarga la página para empezar una sesión nueva."
        )
    if global_usage_today() >= PREMIUM_CHAT_MAX_PER_DAY_GLOBAL:
        return "Se ha alcanzado el límite diario de preguntas al chat experto. Vuelve a intentarlo mañana."
    return None


# --- Llamada a Anthropic -----------------------------------------------

def ask(ticker: str, nombre: str, corpus: str, history: list[dict]) -> str:
    """`history` es una lista de {"role": "user"/"assistant", "content": str}
    (sin mensaje de sistema, eso se construye aquí a partir del corpus).
    Lanza la excepción tal cual si la llamada a la API falla — el
    llamador decide cómo mostrarlo (ver views/premium.py). Solo registra
    el uso (contadores de sesión y globales) si la llamada tuvo éxito."""
    import anthropic

    # Solo hace falta si ANTHROPIC_API_KEY es una clave "identity-linked"
    # sin workspace fijado — ver la nota en src/config.py sobre
    # ANTHROPIC_WORKSPACE_ID. Con una clave ligada a un workspace (o una
    # "workspace key" clásica), esta cabecera de más no molesta ni hace
    # falta, así que es seguro mandarla siempre que esté configurada.
    default_headers = {"anthropic-workspace-id": ANTHROPIC_WORKSPACE_ID} if ANTHROPIC_WORKSPACE_ID else None
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, default_headers=default_headers)
    system = SYSTEM_PROMPT_TEMPLATE.format(ticker=ticker, nombre=nombre, corpus=corpus)
    response = client.messages.create(
        model=PREMIUM_CHAT_MODEL,
        max_tokens=1024,
        system=system,
        messages=history,
    )
    st.session_state[session_usage_key(ticker)] = session_usage(ticker) + 1
    _register_global_usage()
    return response.content[0].text
