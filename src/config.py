"""
Configuración central de Stocker.

Nada de lo que aparece aquí debe quedar hardcodeado dentro de la lógica del
pipeline (descarga, limpieza, features, modelo): cualquier módulo que
necesite el ticker, el rango de histórico o la ruta de la base de datos debe
importarlo desde este fichero. Así se puede escalar a más acciones en el
futuro sin buscar y reemplazar valores sueltos por el código.

Diseño de referencia: ver CONTEXTO.md en la raíz del repo.
"""

from pathlib import Path

# --- Raíz del proyecto ---
BASE_DIR = Path(__file__).resolve().parent.parent

# --- Ticker(s) ---
# Empezó como MVP de una única acción (AAPL); ampliado a un universo
# multi-ticker (ver CONTEXTO.md). La lista la mantiene el usuario a mano
# aquí — no hay API gratuita y oficial de composición de índices (S&P 500,
# etc.), así que no se scrapea de ningún sitio para no meter una fuente de
# datos no documentada en el pipeline.
#
# Universo actual: ~220 tickers de EE.UU. pasados por el usuario, tras
# deduplicar (venían 18 repetidos) y corregir `BRK.B` -> `BRK-B` (formato
# de Yahoo Finance para clases de acciones). Se descartaron 10 tickers
# ambiguos/no verificados en vez de adivinarlos (GEA, XAY, DUKS, AOR, NS,
# UL, HMC, AMX, SPH, MLI — típicos, ADRs extranjeros no S&P 500, o
# tickers de ETF, no de acciones concretas): "solo los que seguro
# funcionan", según el usuario. Los ~43 tickers españoles (IBEX/BME) se
# aparcan aparte hasta resolver el calendario de mercado (pandas_market_
# calendars no tiene calendario de Madrid/BME) — ver CONTEXTO.md.
TICKERS = [
    "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "AVGO", "META", "TSLA", "LLY",
    "BRK-B", "MU", "JPM", "WMT", "AMD", "V", "JNJ", "XOM", "MA", "ABBV",
    "INTC", "CSCO", "BAC", "COST", "AMAT", "CAT", "CVX", "UNH", "GE", "LRCX",
    "KO", "ORCL", "PG", "MS", "HD", "MRK", "GS", "PLTR", "PM", "NFLX",
    "DELL", "RTX", "KLAC", "GEV", "PANW", "WFC", "TXN", "LIN", "C", "AXP",
    "ANET", "TMO", "IBM", "AMGN", "VZ", "TMUS", "MCD", "PEP", "CRWD", "NEE",
    "ABT", "APH", "STX", "SCHW", "ADI", "UNP", "QCOM", "WELL", "TJX", "INTU",
    "BKNG", "DIS", "LOW", "BX", "DHR", "CMCSA", "SPGI", "NOW", "VRT", "COP",
    "PGR", "HON", "NKE", "ISRG", "FI", "BSX", "LMT", "BA", "CB", "EL",
    "DE", "SYK", "BLK", "AON", "KMB", "ADP", "PLD", "CVS", "MCO", "MDT",
    "SBUX", "MO", "T", "GILD", "WMB", "REGN", "D", "CI", "SO", "MMC",
    "PH", "ADM", "TT", "CEG", "FCX", "USB", "ICE", "EOG", "AIG", "TRV",
    "EMR", "SHW", "CMG", "PNC", "ACN", "MCK", "BKR", "RSG", "MAR", "PCAR",
    "MET", "SLB", "SPG", "AZO", "CARR", "COR", "PSX", "ADSK", "O", "TRGP",
    "HCA", "AME", "TGT", "ALL", "KMI", "OXY", "VMC", "FAST", "CTAS", "CMI",
    "LEN", "PRU", "DLR", "MPC", "KR", "HUM", "TEL", "BK", "PAYX", "ODFL",
    "GPN", "ROST", "OTIS", "PPG", "DAL", "VRSK", "ED", "WEC", "EA", "YUM",
    "CPRT", "DHI", "PCG", "DFS", "FDX", "AMP", "GLW", "CSX", "PEG", "IDXX",
    "ITW", "BDX", "DTE", "KHC", "IQV", "SBAC", "EXR", "AWK", "TRP", "EBAY",
    "LHX", "AEE", "WTW", "SYY", "MTB", "FSLR", "TROW", "CBRE", "ROK", "SRE",
    "CDNS", "WDC", "HES", "HAL", "FITB", "HBAN", "EIX", "VTR", "PWR", "STE",
]
DEFAULT_TICKER = TICKERS[0]

# --- Robustez de la descarga en lote ---
# Relevante al escalar de 1 a decenas de tickers: yfinance no es una API
# oficial y puede aplicar rate-limiting si se piden muchos tickers seguidos
# sin pausa (ver CONTEXTO.md, Riesgos conocidos).
DOWNLOAD_DELAY_SECONDS = 1.5  # pausa entre tickers dentro de un mismo lote
DOWNLOAD_RETRY_ATTEMPTS = 3  # intentos por ticker antes de darlo por fallido
DOWNLOAD_RETRY_BACKOFF_SECONDS = 5  # espera entre reintentos de un mismo ticker

# --- Rango de histórico a descargar ---
# CONTEXTO.md: mínimo 3-5 años, idealmente 8-10 si el volumen de filas
# resultante sigue siendo manejable (~1.250 a ~2.500 filas).
HISTORY_PERIOD = "10y"  # formato aceptado por yfinance (p. ej. "5y", "10y", "max")
HISTORY_START = None  # alternativa: fecha ISO "YYYY-MM-DD" para un rango explícito
HISTORY_END = None  # None = hasta la sesión más reciente disponible

# --- Rutas de datos (capas raw / processed / gold) ---
DATA_DIR = BASE_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
GOLD_DATA_DIR = DATA_DIR / "gold"

# --- Base de datos ---
# SQLite en el MVP (ver CONTEXTO.md); migración natural a PostgreSQL si se
# escala a más tickers, sin que el resto del pipeline deba cambiar, siempre
# que solo se acceda a la base de datos a través de DB_URL.
DB_PATH = DATA_DIR / "stocker.db"
DB_URL = f"sqlite:///{DB_PATH}"

# --- Calendario de mercado ---
# Usado para distinguir huecos de calendario (fines de semana / festivos
# NYSE-NASDAQ) de fallos reales de descarga. Ver pandas_market_calendars.
MARKET_CALENDAR = "NYSE"
