"""
src/news.py — noticias y sentimiento vía Alpha Vantage NEWS_SENTIMENT.

Fuente NUEVA respecto al resto del pipeline (yfinance solo cubre precios).
Decisión explícita del usuario (2026-07-30) tras comparar tres opciones —
ver CONTEXTO.md, sección "Noticias y sentimiento": Alpha Vantage
NEWS_SENTIMENT (elegida — sentimiento ya calculado por su NLP, admite
agrupar varios tickers por llamada), Finnhub company-news (límite más
generoso pero sentimiento de pago) y yfinance.news (sin dependencia nueva
pero sin histórico ni score).

Limitación real y aceptada: el free tier de Alpha Vantage son
NEWS_DAILY_CALL_BUDGET peticiones/día EN TOTAL. Con NEWS_TICKERS_PER_CALL
tickers por llamada, completar el backfill de NEWS_BACKFILL_MONTHS meses
lleva varias ejecuciones en varios días — el progreso se persiste en
`news_backfill_progress` (tabla) para poder parar y retomar sin repetir
trabajo ya hecho ni desperdiciar presupuesto de peticiones.

CORREGIDO 2026-08-06: NEWS_TICKERS_PER_CALL bajó de 10 a 1 (ver
config.py) tras descubrir que agrupar varios tickers por llamada
devolvía sistemáticamente 0 artículos (probable semántica AND del
parámetro `tickers`, no OR — ver comentario junto a NEWS_TICKERS_PER_CALL
en config.py para el detalle y la evidencia). `ticker_batches()` sigue
aceptando cualquier tamaño de lote sin cambios de código, simplemente
ahora se le pide tamaño 1.

Capas, igual que el resto del pipeline (raw → processed):
- raw: volcado JSON tal cual de cada llamada, en data/raw/news/ (auditoría,
  igual que download.py con los CSV de precios).
- processed: tabla `news_articles`, upsert por (ticker, url) — dedupe
  natural si dos ventanas de fechas se solapan.
- agregado: VISTA `news_sentiment_daily` (db.py) — nunca tabla física, para
  que no pueda quedar desincronizada de news_articles.

Este módulo NO integra la señal como feature del modelo — eso es un paso
posterior y deliberadamente separado (ver CONTEXTO.md): aquí solo se puebla
la base de datos.

Revisión de auditoría (16/09/2026):
- A3: la actualización diaria pedía solo "ayer -> hoy" a 25 tickers al día y
  los días de los tickers no consultados se perdían (la cobertura cayó de
  ~200 tickers/día a ~20). Ahora cada ticker se pide desde su última fecha
  consultada (tabla `news_fetch_log`) hasta hoy, empezando por los que más
  tiempo llevan sin consultarse.
- A5: los mensajes de error de Alpha Vantage incluyen la clave de la API; se
  enmascaran antes de escribirlos en el log.
- C1: usa el mismo cerrojo que el pipeline diario para no escribir en la
  base de datos a la vez.

Uso:
    python news.py                    # modo automático: backfill si queda pendiente, si no, diario
                                       # (este es el que debe usar la tarea programada)
    python news.py --backfill         # fuerza el backfill aunque ya estuviera completo
    python news.py --daily            # fuerza la actualización incremental (ayer→hoy)
    python news.py --max-calls 10     # presupuesto de llamadas distinto al de config.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import requests
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod  # noqa: E402
from config import (  # noqa: E402
    ALPHA_VANTAGE_API_KEY,
    NEWS_BACKFILL_MONTHS,
    NEWS_BACKFILL_REFERENCE_DATE,
    NEWS_BACKFILL_WINDOW_DAYS,
    NEWS_DAILY_CALL_BUDGET,
    NEWS_DAILY_MAX_WINDOW_DAYS,
    NEWS_PRIORITY_DAILY_SHARE,
    NEWS_PRIORITY_TICKERS,
    NEWS_RAW_DIR,
    NEWS_REQUEST_DELAY_SECONDS,
    NEWS_TICKERS_PER_CALL,
    TICKERS,
)

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"

# CORREGIDO 2026-08-06 — la asunción de abajo era errónea. Se pensó que
# Alpha Vantage esperaba las clases de acciones con punto ("BRK.B") en vez
# de guion ("BRK-B", formato de Yahoo/yfinance, usado en config.TICKERS),
# y se mapeaba BRK-B -> BRK.B antes de cada llamada. En una ejecución real
# (ver CONTEXTO.md, log del 2026-08-06) la propia API devolvió el error:
# "Invalid ticker format: BRK.B. Ticker can only contain alphanumeric
# characters, colons, underscores, and hyphens" — es decir, el punto es
# justo el carácter que Alpha Vantage NO acepta; el guion sí está en su
# lista de caracteres permitidos. Se elimina el mapeo: BRK-B se envía tal
# cual, sin traducir. El mecanismo de override se deja vacío (en vez de
# borrado) por si algún otro ticker necesita uno en el futuro — la
# frontera de traducción con esta API sigue existiendo, solo que ahora no
# hace falta para ningún ticker del universo actual.
TICKER_SYMBOL_OVERRIDES: dict[str, str] = {}
_REVERSE_TICKER_OVERRIDES = {v: k for k, v in TICKER_SYMBOL_OVERRIDES.items()}


def _to_av_symbol(ticker: str) -> str:
    return TICKER_SYMBOL_OVERRIDES.get(ticker, ticker)


def _from_av_symbol(symbol: str) -> str:
    return _REVERSE_TICKER_OVERRIDES.get(symbol, symbol)


_API_KEY_IN_MESSAGE_RE = re.compile(r"(API key as )\S+", re.IGNORECASE)


def mask_secrets(message: str) -> str:
    """Oculta la clave de Alpha Vantage en cualquier texto que vaya al log."""
    message = _API_KEY_IN_MESSAGE_RE.sub(r"\1****", str(message))
    if ALPHA_VANTAGE_API_KEY:
        message = message.replace(ALPHA_VANTAGE_API_KEY, "****")
    return message


class AlphaVantageQuotaError(RuntimeError):
    """Cupo de peticiones agotado (diario u otro) — debe parar TODA la
    ejecución de --backfill, no tiene sentido seguir intentando otros
    lotes/ventanas si la API ya no va a responder a nada más hoy."""


class AlphaVantageRequestError(RuntimeError):
    """Error de parámetros/petición para ESTE lote+ventana en concreto
    (p. ej. un ticker que Alpha Vantage no reconoce) — no debe parar el
    resto de la ejecución, solo este ítem se reintentará más adelante."""


def _looks_like_quota_error(message: str) -> bool:
    m = message.lower()
    return any(kw in m for kw in ("rate limit", "requests per day", "premium", "frequency", "thank you for using"))


def ticker_batches(tickers: list[str] | None = None) -> list[list[str]]:
    """Trocea `tickers` (por defecto config.TICKERS) en lotes de
    NEWS_TICKERS_PER_CALL. El índice en la lista resultante (`batch_id`) es
    solo posicional/informativo para logs — la identidad real de cada lote
    en `news_backfill_progress` es `_batch_key()` (ver más abajo), no este
    índice, precisamente porque cambia de significado si NEWS_TICKERS_PER_CALL
    o TICKERS cambian entre ejecuciones."""
    tickers = tickers if tickers is not None else TICKERS
    return [tickers[i : i + NEWS_TICKERS_PER_CALL] for i in range(0, len(tickers), NEWS_TICKERS_PER_CALL)]


def _batch_key(tickers_in_batch: list[str]) -> str:
    """Identidad estable de un lote = hash de sus tickers exactos (no su
    posición). Si la composición de un lote cambia (porque cambió
    NEWS_TICKERS_PER_CALL o TICKERS), genera una clave distinta en vez de
    heredar por error el progreso de un lote con otros tickers — bug real
    detectado el 2026-07-31 al bajar NEWS_TICKERS_PER_CALL de 50 a 10 (ver
    CONTEXTO.md y db.py, `_migrate_news_backfill_progress`)."""
    joined = ",".join(sorted(tickers_in_batch))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def date_windows(months_back: int, window_days: int, end: dt.date | None = None) -> list[tuple[dt.date, dt.date]]:
    """Ventanas [inicio, fin) de `window_days` días cubriendo los últimos
    `months_back` meses, ordenadas de más antigua a más reciente (el
    backfill prioriza rellenar historia antes que repetir lo más
    reciente)."""
    end = end or dt.date.today()
    start = end - dt.timedelta(days=months_back * 30)
    windows: list[tuple[dt.date, dt.date]] = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + dt.timedelta(days=window_days), end)
        windows.append((cursor, window_end))
        cursor = window_end
    return windows


def _sentiment_label(score: float) -> str:
    """Mismos umbrales que documenta Alpha Vantage para su propio score."""
    if score <= -0.35:
        return "Bearish"
    if score <= -0.15:
        return "Somewhat-Bearish"
    if score < 0.15:
        return "Neutral"
    if score < 0.35:
        return "Somewhat-Bullish"
    return "Bullish"


def fetch_news_batch(tickers: list[str], time_from: dt.date, time_to: dt.date) -> dict:
    """Una llamada a NEWS_SENTIMENT para varios tickers y una ventana de
    fechas. Alpha Vantage comunica tanto el cupo agotado como errores de
    parámetros dentro del cuerpo JSON (claves "Note"/"Information"), no con
    un código HTTP de error — hay que comprobarlo explícitamente Y
    distinguir un caso del otro (ver AlphaVantageQuotaError vs
    AlphaVantageRequestError): un ticker no reconocido no debe tratarse
    como si se hubiera agotado el cupo del día."""
    if not ALPHA_VANTAGE_API_KEY:
        raise RuntimeError(
            "ALPHA_VANTAGE_API_KEY no configurada — copia .env.example a .env y rellena tu clave "
            "(gratis en https://www.alphavantage.co/support/#api-key)."
        )

    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": ",".join(_to_av_symbol(t) for t in tickers),
        "time_from": time_from.strftime("%Y%m%dT0000"),
        "time_to": time_to.strftime("%Y%m%dT0000"),
        "limit": 1000,
        "sort": "RELEVANCE",
        "apikey": ALPHA_VANTAGE_API_KEY,
    }
    resp = requests.get(ALPHA_VANTAGE_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if "Note" in data or "Information" in data:
        message = mask_secrets(str(data.get("Note") or data.get("Information") or data))
        if _looks_like_quota_error(message):
            raise AlphaVantageQuotaError(message)
        raise AlphaVantageRequestError(message)
    if "feed" not in data:
        raise AlphaVantageRequestError(mask_secrets(f"respuesta inesperada de Alpha Vantage: {data}"))
    return data


def save_raw_json(data: dict, batch_id: int, window_start: dt.date) -> Path:
    NEWS_RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = NEWS_RAW_DIR / f"batch{batch_id}_{window_start.isoformat()}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def upsert_articles(data: dict, tickers_in_batch: set[str], engine: Engine) -> int:
    """Extrae de cada artículo solo el sentimiento específico de los
    tickers de este lote (un artículo puede mencionar tickers que no
    pedimos; se ignoran). Devuelve el nº de filas upserted."""
    rows = []
    for article in data.get("feed", []):
        url = article.get("url")
        time_published = article.get("time_published", "")
        try:
            article_date = dt.datetime.strptime(time_published[:8], "%Y%m%d").date()
        except ValueError:
            continue

        for ts in article.get("ticker_sentiment", []):
            # Alpha Vantage devuelve el ticker en SU formato (p. ej.
            # "BRK.B"); se traduce de vuelta al formato canónico del
            # proyecto (BRK-B, el mismo que usa daily_prices/stocks) antes
            # de comprobar/guardar nada.
            ticker = _from_av_symbol(ts.get("ticker"))
            if ticker not in tickers_in_batch:
                continue
            try:
                relevance = float(ts.get("relevance_score"))
                sentiment = float(ts.get("ticker_sentiment_score"))
            except (TypeError, ValueError):
                continue
            rows.append(
                {
                    "ticker": ticker,
                    "url": url,
                    "date": article_date,
                    "title": article.get("title"),
                    "source": article.get("source"),
                    "relevance_score": relevance,
                    "ticker_sentiment_score": sentiment,
                    "ticker_sentiment_label": _sentiment_label(sentiment),
                    "time_published": time_published,
                    "fetched_at": dt.datetime.utcnow(),
                }
            )

    if not rows:
        return 0

    with engine.begin() as conn:
        for row in rows:
            stmt = sqlite_insert(dbmod.news_articles).values(**row)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker", "url"],
                set_={
                    "relevance_score": stmt.excluded.relevance_score,
                    "ticker_sentiment_score": stmt.excluded.ticker_sentiment_score,
                    "ticker_sentiment_label": stmt.excluded.ticker_sentiment_label,
                    "fetched_at": stmt.excluded.fetched_at,
                },
            )
            conn.execute(stmt)
    return len(rows)


def pending_work_items(engine: Engine) -> list[tuple[int, dt.date, dt.date]]:
    """(batch_id, window_start, window_end) aún no completados en
    news_backfill_progress, de más antiguo a más reciente.

    La comprobación de "ya hecho" se hace por `_batch_key(tickers_del_lote)`,
    no por `batch_id` — ver el comentario en `_batch_key()` y en
    `db.news_backfill_progress`. Se consulta vía el objeto Table (no SQL
    crudo con `text()`) para que SQLAlchemy aplique el tipo `Date` al leer
    `window_start` — con SQL crudo, sqlite3 devuelve la fecha como string y
    la comparación con los `datetime.date` de `date_windows()` nunca
    coincidiría.

    `end=NEWS_BACKFILL_REFERENCE_DATE` (no el `dt.date.today()` por
    defecto de `date_windows()`) es OBLIGATORIO aquí — ver el comentario
    junto a esa constante en config.py: sin anclar `end`, las ventanas se
    recalculan con fechas distintas cada día y el backfill nunca converge
    (bug real corregido 2026-08-08)."""
    windows = date_windows(NEWS_BACKFILL_MONTHS, NEWS_BACKFILL_WINDOW_DAYS, end=NEWS_BACKFILL_REFERENCE_DATE)
    batches = ticker_batches()
    batch_keys = [_batch_key(b) for b in batches]

    with engine.begin() as conn:
        done = {
            (row.batch_key, row.window_start)
            for row in conn.execute(
                dbmod.news_backfill_progress.select().with_only_columns(
                    dbmod.news_backfill_progress.c.batch_key,
                    dbmod.news_backfill_progress.c.window_start,
                )
            )
        }

    items = []
    for window_start, window_end in windows:
        for batch_id, key in enumerate(batch_keys):
            if (key, window_start) not in done:
                items.append((batch_id, window_start, window_end))
    return items


def mark_done(engine: Engine, batch_id: int, window_start: dt.date, window_end: dt.date) -> None:
    batches = ticker_batches()
    key = _batch_key(batches[batch_id])
    with engine.begin() as conn:
        stmt = sqlite_insert(dbmod.news_backfill_progress).values(
            batch_key=key,
            batch_id=batch_id,
            window_start=window_start,
            window_end=window_end,
            completed_at=dt.datetime.utcnow().isoformat(),
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["batch_key", "window_start"])
        conn.execute(stmt)


def run_backfill(engine: Engine, max_calls: int) -> None:
    batches = ticker_batches()
    items = pending_work_items(engine)

    if not items:
        print("[news] backfill ya completo — no quedan ventanas pendientes.", file=sys.stderr)
        return

    total_windows = len(date_windows(NEWS_BACKFILL_MONTHS, NEWS_BACKFILL_WINDOW_DAYS, end=NEWS_BACKFILL_REFERENCE_DATE)) * len(batches)
    print(
        f"[news] {len(items)}/{total_windows} ventanas pendientes (lote de tickers x ventana de fechas). "
        f"Presupuesto de esta ejecución: {max_calls} llamadas.",
        file=sys.stderr,
    )

    calls_made = 0
    failed_batches: set[int] = set()
    for batch_id, window_start, window_end in items:
        if calls_made >= max_calls:
            break
        tickers_in_batch = set(batches[batch_id])
        try:
            data = fetch_news_batch(list(tickers_in_batch), window_start, window_end)
            save_raw_json(data, batch_id, window_start)
            n = upsert_articles(data, tickers_in_batch, engine)
            mark_done(engine, batch_id, window_start, window_end)
            print(
                f"[news] lote {batch_id} ({window_start}→{window_end}): {n} filas de sentimiento guardadas",
                file=sys.stderr,
            )
        except AlphaVantageQuotaError as exc:
            # Cupo agotado: no tiene sentido seguir gastando llamadas hoy,
            # se para TODA la ejecución (no solo este ítem).
            print(f"[news] cupo de Alpha Vantage agotado — parando esta ejecución ({mask_secrets(exc)})", file=sys.stderr)
            calls_made += 1
            break
        except Exception as exc:  # noqa: BLE001
            # Error de parámetros de ESTE lote+ventana (p. ej. un ticker
            # que Alpha Vantage no reconoce) — no se marca como hecho (se
            # reintenta en la próxima ejecución), pero SÍ seguimos con el
            # resto de items en esta misma ejecución: un lote roto no debe
            # bloquear el progreso del resto del backfill.
            print(
                f"[news] lote {batch_id} ({window_start}→{window_end}): FALLÓ, se reintentará en la "
                f"próxima ejecución ({mask_secrets(exc)})",
                file=sys.stderr,
            )
            failed_batches.add(batch_id)

        calls_made += 1
        time.sleep(NEWS_REQUEST_DELAY_SECONDS)

    remaining = len(pending_work_items(engine))
    print(
        f"[news] {calls_made} llamada(s) hecha(s) en esta ejecución. Quedan {remaining} ventanas pendientes.",
        file=sys.stderr,
    )
    if failed_batches:
        print(
            f"[news] AVISO: el/los lote(s) {sorted(failed_batches)} fallaron en TODAS sus ventanas de esta "
            "ejecución — probablemente un ticker de ese lote no lo reconoce Alpha Vantage (revisa el mensaje "
            "de error de arriba), no un problema de cupo. No se resolverá solo reintentando.",
            file=sys.stderr,
        )


def _last_fetch_dates(engine: Engine) -> dict[str, dt.date]:
    """Última fecha consultada por ticker: `news_fetch_log` si existe; si
    no, la fecha del artículo más reciente guardado (para arrancar la tabla
    sobre una base de datos que ya tenía noticias)."""
    from sqlalchemy import func, select

    with engine.begin() as conn:
        logged = {
            r.ticker: r.last_time_to
            for r in conn.execute(select(dbmod.news_fetch_log.c.ticker, dbmod.news_fetch_log.c.last_time_to))
        }
        from_articles = {
            r[0]: r[1]
            for r in conn.execute(
                select(dbmod.news_articles.c.ticker, func.max(dbmod.news_articles.c.date))
                .group_by(dbmod.news_articles.c.ticker)
            )
        }
    out = {}
    for ticker in TICKERS:
        d = logged.get(ticker) or from_articles.get(ticker)
        if isinstance(d, str):
            d = dt.date.fromisoformat(d[:10])
        if d is not None:
            out[ticker] = d
    return out


def _mark_fetched(engine: Engine, ticker: str, time_to: dt.date) -> None:
    stmt = sqlite_insert(dbmod.news_fetch_log).values(
        ticker=ticker, last_time_to=time_to, updated_at=dt.datetime.now().isoformat(timespec="seconds"),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker"],
        set_={"last_time_to": stmt.excluded.last_time_to, "updated_at": stmt.excluded.updated_at},
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def plan_daily_update(
    last_fetch: dict[str, dt.date], today: dt.date, max_calls: int
) -> list[tuple[str, dt.date, dt.date, bool]]:
    """Qué tickers consultar hoy y con qué ventana.

    - Reparto del presupuesto igual que antes: NEWS_PRIORITY_DAILY_SHARE
      para NEWS_PRIORITY_TICKERS y el resto para los demás.
    - Dentro de cada grupo, primero los que llevan más tiempo sin
      consultarse (los nunca consultados, antes que nadie). Así nadie se
      queda atrás indefinidamente y no hace falta azar.
    - Ventana: desde la última fecha consultada (se repite ese día por si
      llegaron artículos más tarde; el upsert evita duplicados) hasta
      mañana, limitada a NEWS_DAILY_MAX_WINDOW_DAYS.

    Devuelve (ticker, time_from, time_to, es_prioritario)."""
    priority_set = set(NEWS_PRIORITY_TICKERS)
    never = dt.date.min

    def order(tickers):
        return sorted(tickers, key=lambda t: (last_fetch.get(t, never), TICKERS.index(t)))

    priority = order([t for t in TICKERS if t in priority_set])
    others = order([t for t in TICKERS if t not in priority_set])
    n_priority = min(len(priority), max(1, round(max_calls * NEWS_PRIORITY_DAILY_SHARE))) if priority else 0
    n_others = min(len(others), max(0, max_calls - n_priority))
    # Si un grupo no agota su parte, el sobrante pasa al otro.
    n_priority = min(len(priority), max_calls - n_others)

    time_to = today + dt.timedelta(days=1)
    earliest = today - dt.timedelta(days=NEWS_DAILY_MAX_WINDOW_DAYS)
    plan = []
    for group, n, is_priority in ((priority, n_priority, True), (others, n_others, False)):
        for ticker in group[:n]:
            start = last_fetch.get(ticker, earliest)
            start = max(start, earliest)
            plan.append((ticker, start, time_to, is_priority))
    return plan


def run_daily_update(engine: Engine, max_calls: int = NEWS_DAILY_CALL_BUDGET) -> None:
    """Actualización incremental por ticker desde su última consulta (ver
    `plan_daily_update`). Para en cuanto Alpha Vantage indica que el cupo
    diario está agotado, sin gastar más llamadas en errores."""
    today = dt.date.today()
    plan = plan_daily_update(_last_fetch_dates(engine), today, max_calls)

    calls_made = ok = 0
    for ticker, time_from, time_to, is_priority in plan:
        etiqueta = "prioritario" if is_priority else "resto"
        try:
            data = fetch_news_batch([ticker], time_from, time_to)
            batch_id = TICKERS.index(ticker)
            save_raw_json(data, batch_id, time_from)
            n = upsert_articles(data, {ticker}, engine)
            _mark_fetched(engine, ticker, today)
            ok += 1
            print(f"[news] {ticker} ({etiqueta}, {time_from}→{today}): {n} filas de sentimiento", file=sys.stderr)
        except AlphaVantageQuotaError as exc:
            calls_made += 1
            print(f"[news] cupo de Alpha Vantage agotado — parando ({mask_secrets(exc)})", file=sys.stderr)
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[news] {ticker} ({etiqueta}): FALLÓ la actualización ({mask_secrets(exc)})", file=sys.stderr)
        calls_made += 1
        time.sleep(NEWS_REQUEST_DELAY_SECONDS)

    print(
        f"[news] actualización diaria: {ok}/{calls_made} llamada(s) correctas de {len(plan)} planificadas "
        f"(universo de {len(TICKERS)} tickers).",
        file=sys.stderr,
    )


def run_auto(engine: Engine, max_calls: int) -> None:
    """Modo pensado para ejecución programada (Task Scheduler/cron), sin
    intervención manual: mientras queden ventanas de backfill pendientes,
    avanza el backfill; en cuanto esté completo, cambia solo a la
    actualización diaria. Así el MISMO comando programado sirve durante los
    ~9 días que tarda el backfill (208 tickers x 1 llamada, ver
    config.NEWS_TICKERS_PER_CALL) y para siempre después, sin tener que
    volver a tocar el flag."""
    if pending_work_items(engine):
        run_backfill(engine, max_calls=max_calls)
    else:
        print("[news] backfill completo — modo automático pasa a actualización diaria.", file=sys.stderr)
        run_daily_update(engine, max_calls=max_calls)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=False)
    mode.add_argument("--backfill", action="store_true", help="fuerza el backfill histórico")
    mode.add_argument("--daily", action="store_true", help="fuerza la actualización incremental (ayer→hoy)")
    parser.add_argument("--no-lock", action="store_true", help=argparse.SUPPRESS)  # lo usa run_pipeline.py, que ya tiene el cerrojo
    parser.add_argument(
        "--max-calls",
        type=int,
        default=NEWS_DAILY_CALL_BUDGET,
        help="presupuesto de llamadas para esta ejecución (por defecto, config.NEWS_DAILY_CALL_BUDGET)",
    )
    args = parser.parse_args(argv)

    def _run() -> int:
        engine = dbmod.get_engine()
        dbmod.init_db(engine)
        dbmod.seed_stocks(engine, tickers=TICKERS)

        if args.backfill:
            run_backfill(engine, max_calls=args.max_calls)
        elif args.daily:
            run_daily_update(engine, max_calls=args.max_calls)
        else:
            # Sin flag: modo automático — es el que debe usar la tarea
            # programada (ver run_news_daily.bat).
            run_auto(engine, max_calls=args.max_calls)
        return 0

    if args.no_lock:
        return _run()
    from pipeline_lock import pipeline_lock

    with pipeline_lock("news"):
        return _run()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
