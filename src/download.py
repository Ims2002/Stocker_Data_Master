"""
src/download.py — descarga del histórico diario (capa raw) vía yfinance.

Descarga el histórico OHLCV diario del/los ticker(s) definidos en
`config.py` y lo vuelca, sin transformar, en `data/raw/` como copia de
auditoría de qué se descargó y cuándo (ver CONTEXTO.md). No hace ninguna
limpieza, tipado ni upsert aquí — eso es responsabilidad de la capa
processed (`daily_prices`, módulo `load.py`).

Fuente única: la API de Yahoo Finance vía `yfinance`. Si esta fuente falla
de forma persistente, existe un fallback documentado (no activo en el
pipeline) en `src/fallback/yahooquery_download.py`.

Pensado para lotes de varios tickers (no solo AAPL): cada ticker se
descarga de forma aislada, con reintentos, y un fallo en uno no aborta el
resto del lote (ver CONTEXTO.md, riesgo de rate-limiting de yfinance al
escalar a ~50 tickers). Al final se informa de qué tickers fallaron, si
alguno lo hizo.

Uso:
    python download.py                  # descarga todos los TICKERS de config.py
    python download.py AAPL MSFT        # descarga tickers concretos
"""

from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

# Permite ejecutar este fichero directamente (python src/download.py) sin
# depender de que src/ esté instalado como paquete.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    DEFAULT_TICKER,
    DOWNLOAD_DELAY_SECONDS,
    DOWNLOAD_RETRY_ATTEMPTS,
    DOWNLOAD_RETRY_BACKOFF_SECONDS,
    HISTORY_END,
    HISTORY_PERIOD,
    HISTORY_START,
    RAW_DATA_DIR,
    TICKERS,
)


def download_ticker_history(ticker: str = DEFAULT_TICKER) -> pd.DataFrame:
    """Descarga el histórico diario OHLCV de `ticker` vía yfinance.

    Usa HISTORY_PERIOD (config.py) si no hay HISTORY_START definido; si
    HISTORY_START sí está definido, se usa el rango start/end explícito.

    `auto_adjust=False` es intencional: se necesita conservar `Close` y
    `Adj Close` como columnas separadas (CONTEXTO.md usa `Adj Close` para
    retornos/medias móviles, pero no descarta ninguna columna de la API).
    """
    t = yf.Ticker(ticker)

    if HISTORY_START:
        df = t.history(start=HISTORY_START, end=HISTORY_END, interval="1d", auto_adjust=False)
    else:
        df = t.history(period=HISTORY_PERIOD, interval="1d", auto_adjust=False)

    if df.empty:
        raise RuntimeError(f"yfinance no devolvió datos para {ticker}")

    return df


def save_raw_csv(df: pd.DataFrame, ticker: str = DEFAULT_TICKER) -> Path:
    """Vuelca el DataFrame tal cual a data/raw/, sin transformar.

    Un fichero por descarga, nombrado con la fecha de descarga (no de
    mercado) — ej. `aapl_20260727.csv` — como copia de auditoría. La
    limpieza, el tipado y la deduplicación se hacen en la capa processed,
    no aquí.
    """
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().strftime("%Y%m%d")
    path = RAW_DATA_DIR / f"{ticker.lower()}_{today}.csv"
    df.to_csv(path)
    print(f"[download] {ticker}: {len(df)} filas → {path}", file=sys.stderr)
    return path


def download_ticker_with_retry(ticker: str) -> pd.DataFrame:
    """Reintenta la descarga de un ticker hasta DOWNLOAD_RETRY_ATTEMPTS
    veces, con una pausa entre intentos. Propaga la última excepción si
    se agotan los reintentos (el llamador decide si eso debe tumbar todo
    el lote o solo ese ticker)."""
    last_exc: Exception | None = None
    for attempt in range(1, DOWNLOAD_RETRY_ATTEMPTS + 1):
        try:
            return download_ticker_history(ticker)
        except Exception as exc:  # noqa: BLE001 — se relanza si se agotan los intentos
            last_exc = exc
            print(
                f"[download] {ticker}: intento {attempt}/{DOWNLOAD_RETRY_ATTEMPTS} falló ({exc})",
                file=sys.stderr,
            )
            if attempt < DOWNLOAD_RETRY_ATTEMPTS:
                time.sleep(DOWNLOAD_RETRY_BACKOFF_SECONDS)
    assert last_exc is not None
    raise last_exc


def main(tickers: list[str] | None = None) -> int:
    """Descarga cada ticker de forma aislada: un fallo (tras agotar
    reintentos) se registra y se pasa al siguiente ticker, no aborta el
    lote completo. Devuelve el nº de tickers que fallaron (0 = todo bien)."""
    targets = tickers or TICKERS
    failed: list[str] = []

    for i, ticker in enumerate(targets):
        try:
            df = download_ticker_with_retry(ticker)
            save_raw_csv(df, ticker)
        except Exception as exc:  # noqa: BLE001
            print(f"[download] {ticker}: DESCARTADO tras agotar reintentos ({exc})", file=sys.stderr)
            failed.append(ticker)

        if i < len(targets) - 1:
            time.sleep(DOWNLOAD_DELAY_SECONDS)

    if failed:
        print(f"[download] {len(failed)}/{len(targets)} tickers fallaron: {failed}", file=sys.stderr)
    else:
        print(f"[download] {len(targets)}/{len(targets)} tickers descargados sin errores", file=sys.stderr)

    return len(failed)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or None))
