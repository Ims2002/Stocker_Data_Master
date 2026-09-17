"""
src/market_time.py — ¿qué sesiones de mercado están ya cerradas?

Nace de la auditoría del 16/09/2026 (hallazgo C2): el pipeline diario se
ejecutaba hacia las 16:20 (hora de Madrid), con la bolsa de EE. UU. abierta
desde las 15:30. yfinance devuelve entonces una fila para la sesión en curso
con un precio y un volumen de media sesión, que se guardaba como cierre
definitivo: las predicciones partían de ese precio y `track_predictions.py`
resolvía con él (507 de 4.544 resultados reales eran incorrectos).

Todas las comprobaciones usan el calendario de `config.MARKET_CALENDAR`
(NYSE), con los horarios reales de cada sesión (incluidos los cierres
anticipados), nunca una hora fija.
"""

from __future__ import annotations

import datetime as dt
import sys
from functools import lru_cache
from pathlib import Path

import pandas as pd
import pandas_market_calendars as mcal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import MARKET_CALENDAR, MARKET_CLOSE_BUFFER_MINUTES  # noqa: E402


@lru_cache(maxsize=1)
def _calendar():
    return mcal.get_calendar(MARKET_CALENDAR)


def now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _schedule(start: dt.date, end: dt.date) -> pd.DataFrame:
    return _calendar().schedule(start_date=start, end_date=end)


def session_closed(session_date: dt.date, now: pd.Timestamp | None = None) -> bool:
    """True si `session_date` es una sesión de mercado que ya ha cerrado
    (más `MARKET_CLOSE_BUFFER_MINUTES` de margen para que yfinance publique
    el cierre definitivo). Un día no bursátil devuelve True (no hay nada
    pendiente de cerrar)."""
    now = now or now_utc()
    sched = _schedule(session_date, session_date)
    if sched.empty:
        return True
    close = sched["market_close"].iloc[0]
    return now >= close + pd.Timedelta(minutes=MARKET_CLOSE_BUFFER_MINUTES)


def last_closed_session(now: pd.Timestamp | None = None) -> dt.date:
    """Última sesión de mercado completamente cerrada a fecha de `now`."""
    now = now or now_utc()
    today = now.tz_convert("America/New_York").date()
    sched = _schedule(today - dt.timedelta(days=15), today)
    closes = sched["market_close"] + pd.Timedelta(minutes=MARKET_CLOSE_BUFFER_MINUTES)
    closed = sched[closes <= now]
    if closed.empty:
        raise RuntimeError("No se encontró ninguna sesión cerrada en los últimos 15 días")
    return closed.index[-1].date()


def next_session_open(after_date: dt.date) -> pd.Timestamp:
    """Hora de apertura (UTC) de la primera sesión posterior a `after_date`."""
    sched = _schedule(after_date + dt.timedelta(days=1), after_date + dt.timedelta(days=15))
    if sched.empty:
        raise RuntimeError(f"No se encontró ninguna sesión después de {after_date}")
    return sched["market_open"].iloc[0]


def market_is_open(now: pd.Timestamp | None = None) -> bool:
    """True si ahora mismo hay una sesión abierta o recién cerrada (dentro
    del margen), es decir, si los datos de hoy todavía no son definitivos."""
    now = now or now_utc()
    today = now.tz_convert("America/New_York").date()
    sched = _schedule(today, today)
    if sched.empty:
        return False
    open_, close = sched["market_open"].iloc[0], sched["market_close"].iloc[0]
    return open_ <= now < close + pd.Timedelta(minutes=MARKET_CLOSE_BUFFER_MINUTES)
