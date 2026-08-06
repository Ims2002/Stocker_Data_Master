"""
Configuración central de Stocker.

Nada de lo que aparece aquí debe quedar hardcodeado dentro de la lógica del
pipeline (descarga, limpieza, features, modelo): cualquier módulo que
necesite el ticker, el rango de histórico o la ruta de la base de datos debe
importarlo desde este fichero. Así se puede escalar a más acciones en el
futuro sin buscar y reemplazar valores sueltos por el código.

Diseño de referencia: ver CONTEXTO.md en la raíz del repo.
"""

import os
from pathlib import Path

# --- Raíz del proyecto ---
BASE_DIR = Path(__file__).resolve().parent.parent

# Carga .env si existe (python-dotenv es opcional pero recomendado; sin él,
# ALPHA_VANTAGE_API_KEY debe exportarse como variable de entorno real). No
# se falla si el paquete no está instalado, para no romper el resto del
# pipeline (descarga/carga/modelo) que no depende de esto.
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

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
#
# Correcciones tras la primera ejecución real de download.py/enrich_stocks.py
# (5 tickers fallaron consistentemente en ambos, verificado con búsqueda):
# - "FI" -> "FISV": error de origen, no un cambio reciente. Fiserv cotiza
#   como FISV en NASDAQ; "FI" es su listado cruzado en Toronto (FI.TO).
# - "MMC" -> "MRSH": Marsh & McLennan cambió de ticker el 14/01/2026 (no
#   deslistada, solo cambio de símbolo, coincidiendo con el cambio de
#   marca a "Marsh").
# - "BK" -> "BNY": Bank of New York Mellon cambió de ticker el 21/05/2026
#   (tampoco deslistada, cambio de símbolo para alinearse con la marca BNY).
# - "DFS" eliminado: Discover Financial Services, absorbida por Capital
#   One (fusión completada el 18-19/05/2025); la acción dejó de cotizar.
# - "HES" eliminado: Hess Corporation, adquirida por Chevron (fusión
#   completada el 18/07/2025); la acción dejó de cotizar.
TICKERS = [
    "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "AVGO", "META", "TSLA", "LLY",
    "BRK-B", "MU", "JPM", "WMT", "AMD", "V", "JNJ", "XOM", "MA", "ABBV",
    "INTC", "CSCO", "BAC", "COST", "AMAT", "CAT", "CVX", "UNH", "GE", "LRCX",
    "KO", "ORCL", "PG", "MS", "HD", "MRK", "GS", "PLTR", "PM", "NFLX",
    "DELL", "RTX", "KLAC", "GEV", "PANW", "WFC", "TXN", "LIN", "C", "AXP",
    "ANET", "TMO", "IBM", "AMGN", "VZ", "TMUS", "MCD", "PEP", "CRWD", "NEE",
    "ABT", "APH", "STX", "SCHW", "ADI", "UNP", "QCOM", "WELL", "TJX", "INTU",
    "BKNG", "DIS", "LOW", "BX", "DHR", "CMCSA", "SPGI", "NOW", "VRT", "COP",
    "PGR", "HON", "NKE", "ISRG", "FISV", "BSX", "LMT", "BA", "CB", "EL",
    "DE", "SYK", "BLK", "AON", "KMB", "ADP", "PLD", "CVS", "MCO", "MDT",
    "SBUX", "MO", "T", "GILD", "WMB", "REGN", "D", "CI", "SO", "MRSH",
    "PH", "ADM", "TT", "CEG", "FCX", "USB", "ICE", "EOG", "AIG", "TRV",
    "EMR", "SHW", "CMG", "PNC", "ACN", "MCK", "BKR", "RSG", "MAR", "PCAR",
    "MET", "SLB", "SPG", "AZO", "CARR", "COR", "PSX", "ADSK", "O", "TRGP",
    "HCA", "AME", "TGT", "ALL", "KMI", "OXY", "VMC", "FAST", "CTAS", "CMI",
    "LEN", "PRU", "DLR", "MPC", "KR", "HUM", "TEL", "BNY", "PAYX", "ODFL",
    "GPN", "ROST", "OTIS", "PPG", "DAL", "VRSK", "ED", "WEC", "EA", "YUM",
    "CPRT", "DHI", "PCG", "FDX", "AMP", "GLW", "CSX", "PEG", "IDXX",
    "ITW", "BDX", "DTE", "KHC", "IQV", "SBAC", "EXR", "AWK", "TRP", "EBAY",
    "LHX", "AEE", "WTW", "SYY", "MTB", "FSLR", "TROW", "CBRE", "ROK", "SRE",
    "CDNS", "WDC", "HAL", "FITB", "HBAN", "EIX", "VTR", "PWR", "STE",
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

# --- Capa gold (features / target) ---
# Nº mínimo de sesiones de histórico que debe tener un ticker en
# daily_prices para poder construir aunque sea una fila de gold_train.
# Antes de la ampliación de features (2026-08-06) el cuello de botella era
# ma_20/volatility_10d (20 sesiones); ahora es MACD, que necesita 33
# sesiones de warm-up (ver features.py, _MACD_WARMUP) — se sube el mínimo
# a 40 (33 + margen) para no descartar en silencio tickers que antes sí
# producían filas de gold_train. Por debajo de esto, gold.py descarta el
# ticker con un aviso en vez de fallar todo el lote.
MIN_HISTORY_ROWS_FOR_GOLD = 40

# --- Modelo ---
# Split temporal (nunca aleatorio, ver CONTEXTO.md): los últimos
# TEST_PERIOD_MONTHS meses de gold_train se usan como test, el resto como
# train. Corte único por fecha global (no por ticker), para que todos los
# tickers compartan el mismo punto de corte train/test.
TEST_PERIOD_MONTHS = 6
MODELS_DIR = BASE_DIR / "models"

# --- Noticias y sentimiento (Alpha Vantage) ---
# Fuente NUEVA respecto al resto del pipeline (yfinance cubre solo precios).
# Decisión explícita del usuario (2026-07-30) tras comparar tres opciones —
# ver CONTEXTO.md, sección "Noticias y sentimiento":
#   - Alpha Vantage NEWS_SENTIMENT (ELEGIDA): da un score de sentimiento ya
#     calculado por su propio NLP — no hay que entrenar/aplicar un modelo
#     propio — y admite agrupar varios tickers por llamada.
#   - Finnhub company-news: límite de peticiones más generoso, pero su
#     endpoint de sentimiento es de pago; el free tier solo da titulares.
#   - yfinance.news: sin dependencia nueva, pero solo titulares muy
#     recientes (no sirve para reconstruir histórico) y tampoco trae score.
ALPHA_VANTAGE_API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")

# Free tier de Alpha Vantage: 25 peticiones/día EN TOTAL, compartidas entre
# todos los endpoints que se usen (no solo NEWS_SENTIMENT). Este número es
# el presupuesto de llamadas por ejecución de news.py --backfill; súbelo si
# se contrata un plan de pago.
NEWS_DAILY_CALL_BUDGET = 25
NEWS_REQUEST_DELAY_SECONDS = 2  # pausa entre llamadas dentro de una misma ejecución

# 208 tickers agrupados de NEWS_TICKERS_PER_CALL en NEWS_TICKERS_PER_CALL
# por llamada (comma-separated en el parámetro `tickers` de la API).
#
# EMPÍRICO (no documentado por Alpha Vantage, descubierto en ejecución real
# el 2026-07-30 — ver CONTEXTO.md): con 50 tickers por llamada, la API
# devuelve "Invalid inputs" de forma consistente en TODOS los lotes de 50,
# incluidos los que no contienen ningún ticker con formato raro (se probó
# la hipótesis de que era `BRK-B` y quedó descartada: los lotes 1-3, sin
# `BRK-B`, fallaron igual). El único lote que funcionó fue el último, de
# solo 8 tickers (el resto de dividir 208/50). Conclusión: el límite real
# de tickers por llamada está en algún punto entre 8 y 50 — se deja en 10
# como valor conservador hasta confirmar el límite exacto (subir este
# número ahorraría llamadas, pero cada intento fallido gasta una llamada
# real del cupo diario, así que no merece la pena tantear a ciegas).
NEWS_TICKERS_PER_CALL = 10

# Backfill inicial: profundidad máxima razonable en el free tier (~2 años,
# decisión explícita del usuario), en ventanas de NEWS_BACKFILL_WINDOW_DAYS
# días para no agotar el límite de artículos por llamada de golpe. Con la
# configuración por defecto: 21 lotes de tickers (208/10) x ~24 ventanas
# mensuales ≈ 504 llamadas ≈ ~21 días de ejecuciones a
# NEWS_DAILY_CALL_BUDGET/día (más lento que la estimación inicial de ~5
# días, hecha antes de descubrir el límite real de tickers por llamada).
# El progreso se guarda en la tabla news_backfill_progress para poder parar
# y retomar entre ejecuciones (ver src/news.py).
NEWS_BACKFILL_MONTHS = 24
NEWS_BACKFILL_WINDOW_DAYS = 30

NEWS_RAW_DIR = DATA_DIR / "raw" / "news"
