#!/usr/bin/env python3
"""
fallback/yahooquery_download.py — descarga alternativa de histórico OHLCV
---------------------------------------------------------------------------
Fallback documentado, NO integrado en el pipeline activo. El pipeline real
usa `src/download.py` (yfinance), según el diseño cerrado en CONTEXTO.md.

Se conserva por si `yfinance` deja de funcionar de forma fiable — riesgo ya
señalado en CONTEXTO.md: no es una API oficial, y cambios internos en la
web de Yahoo Finance pueden romperla temporalmente. `yahooquery` consulta
la misma fuente (Yahoo Finance) mediante un cliente no oficial distinto:
no es una fuente independiente como Alpha Vantage o Stooq (las alternativas
ya documentadas en CONTEXTO.md), pero puede seguir funcionando en el caso
concreto de que sea específicamente `yfinance` el que se rompa.

Origen: versión recortada de un script de referencia aportado por el
usuario (originalmente cubría también market movers, sector/industry,
streaming en vivo, búsqueda y screener de acciones). Se ha recortado a
solo lo que el proyecto necesita — histórico diario OHLCV — porque todo lo
demás queda fuera del alcance del MVP definido en CONTEXTO.md.

DEPENDENCIA (no incluida en requirements.txt por defecto — instalar solo
si hace falta activar este fallback):
    pip install yahooquery

USO
    python yahooquery_download.py ticker AAPL --period 10y
    python yahooquery_download.py download AAPL MSFT --period 1y
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

# Reutiliza la misma configuración central que src/download.py (RAW_DATA_DIR),
# para que este fallback escriba en el mismo sitio que el pipeline activo.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import RAW_DATA_DIR  # noqa: E402

PERIOD_CHOICES = ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"]


def _yq():
    try:
        import yahooquery
        return yahooquery
    except ImportError:
        raise SystemExit("yahooquery no está instalado. Ejecuta: pip install yahooquery")


def _save_raw_csv(df, ticker: str, label: str) -> None:
    import pandas as pd

    if not isinstance(df, pd.DataFrame) or df.empty:
        print(f"[{label}] no se han devuelto datos para {ticker}", file=sys.stderr)
        return
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().strftime("%Y%m%d")
    path = RAW_DATA_DIR / f"{ticker.lower()}_{today}_yahooquery.csv"
    df.to_csv(path)
    print(f"[{label}] {ticker}: {len(df)} filas → {path}", file=sys.stderr)


def cmd_ticker(args):
    lib = _yq()
    sym = args.symbol.upper()
    t = lib.Ticker(sym)
    df = t.history(period=args.period, interval="1d")
    if hasattr(df.index, "nlevels") and df.index.nlevels > 1:
        df = df.droplevel(0)
    _save_raw_csv(df, sym, "ticker/history")


def cmd_download(args):
    lib = _yq()
    syms = [s.upper() for s in args.symbols]
    df = lib.download(tickers=syms, period=args.period, interval="1d")
    for sym in syms:
        sub = df.xs(sym, level=0) if hasattr(df.index, "nlevels") and df.index.nlevels > 1 else df
        _save_raw_csv(sub, sym, "download")


def _build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="yahooquery_download.py",
        description="Fallback de descarga OHLCV diario vía yahooquery (no activo en el pipeline).",
    )
    sub = root.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("ticker", help="Histórico diario de un único ticker")
    sp.add_argument("symbol")
    sp.add_argument("--period", choices=PERIOD_CHOICES, default="10y")
    sp.set_defaults(func=cmd_ticker)

    sp = sub.add_parser("download", help="Histórico diario de varios tickers")
    sp.add_argument("symbols", nargs="+")
    sp.add_argument("--period", choices=PERIOD_CHOICES, default="10y")
    sp.set_defaults(func=cmd_download)

    return root


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
