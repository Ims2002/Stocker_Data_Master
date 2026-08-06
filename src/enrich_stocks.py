"""
src/enrich_stocks.py — rellena metadatos de la tabla `stocks` vía yfinance.

Las columnas `nombre`, `sector` y `pais` de `stocks` existen en el esquema
desde que se amplió el proyecto a multi-ticker (ver CONTEXTO.md — `pais`
se añadió explícitamente "para poder filtrar tickers por país"), pero
hasta ahora nada las rellenaba: `db.seed_stocks()` solo inserta el
`ticker`. Este módulo cierra ese hueco.

No es una fuente de datos nueva: usa `Ticker.info` de la misma librería
`yfinance` ya decidida como fuente única de precios (ver CONTEXTO.md), que
además de histórico también expone metadatos básicos de la compañía
(nombre largo, sector, país de la sede). Noticias/sentimiento SÍ serían
una fuente nueva (Alpha Vantage u otra) — eso queda aparte, pendiente de
decisión explícita, y no se toca en este módulo.

Igual que download.py: cada ticker se procesa de forma aislada, con
reintentos y pausa entre tickers, para no tumbar el lote completo por un
fallo puntual de la API.

Uso:
    python enrich_stocks.py                # todos los tickers de config.TICKERS
    python enrich_stocks.py AAPL MSFT       # tickers concretos
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import yfinance as yf
from sqlalchemy.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod  # noqa: E402
from config import (  # noqa: E402
    DOWNLOAD_DELAY_SECONDS,
    DOWNLOAD_RETRY_ATTEMPTS,
    DOWNLOAD_RETRY_BACKOFF_SECONDS,
    TICKERS,
)


def fetch_ticker_metadata(ticker: str) -> dict:
    """Consulta yfinance por nombre/sector/país de un ticker.

    `Ticker.info` puede no traer todos los campos según el tipo de
    instrumento (ETFs, por ejemplo, no tienen "sector"); los campos
    ausentes quedan como None en vez de fallar.
    """
    info = yf.Ticker(ticker).get_info()
    if not info or info.get("regularMarketPrice") is None and not info.get("longName"):
        raise RuntimeError(f"yfinance no devolvió metadatos usables para {ticker}")

    return {
        "nombre": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "pais": info.get("country"),
    }


def fetch_with_retry(ticker: str) -> dict:
    last_exc: Exception | None = None
    for attempt in range(1, DOWNLOAD_RETRY_ATTEMPTS + 1):
        try:
            return fetch_ticker_metadata(ticker)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            print(
                f"[enrich] {ticker}: intento {attempt}/{DOWNLOAD_RETRY_ATTEMPTS} falló ({exc})",
                file=sys.stderr,
            )
            if attempt < DOWNLOAD_RETRY_ATTEMPTS:
                time.sleep(DOWNLOAD_RETRY_BACKOFF_SECONDS)
    assert last_exc is not None
    raise last_exc


def update_stock_metadata(ticker: str, metadata: dict, engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            dbmod.stocks.update().where(dbmod.stocks.c.ticker == ticker).values(**metadata)
        )


def main(tickers: list[str] | None = None) -> int:
    engine = dbmod.get_engine()
    dbmod.init_db(engine)

    targets = tickers or TICKERS
    dbmod.seed_stocks(engine, tickers=targets)  # por si algún ticker aún no existe en stocks

    failed: list[str] = []
    for i, ticker in enumerate(targets):
        try:
            metadata = fetch_with_retry(ticker)
            update_stock_metadata(ticker, metadata, engine)
            print(f"[enrich] {ticker}: {metadata}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"[enrich] {ticker}: DESCARTADO tras agotar reintentos ({exc})", file=sys.stderr)
            failed.append(ticker)

        if i < len(targets) - 1:
            time.sleep(DOWNLOAD_DELAY_SECONDS)

    if failed:
        print(f"[enrich] {len(failed)}/{len(targets)} tickers fallaron: {failed}", file=sys.stderr)
    else:
        print(f"[enrich] {len(targets)}/{len(targets)} tickers enriquecidos sin errores", file=sys.stderr)

    return len(failed)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or None))
