"""
Configuración central de Stocker.

Nada de lo que aparece aquí debe quedar hardcodeado dentro de la lógica del
pipeline (descarga, limpieza, features, modelo): cualquier módulo que
necesite el ticker, el rango de histórico o la ruta de la base de datos debe
importarlo desde este fichero. Así se puede escalar a más acciones en el
futuro sin buscar y reemplazar valores sueltos por el código.

Diseño de referencia: ver CONTEXTO.md en la raíz del repo.
"""

import datetime as dt
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
# - "EA" eliminado (auditoría 16/09/2026, A2): Electronic Arts dejó de
#   cotizar en NASDAQ el 04/08/2026 tras su compra por PIF, Silver Lake y
#   Affinity Partners. yfinance no devolvía datos desde entonces y se seguía
#   emitiendo una predicción diaria con features del 10/08.
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
    "GPN", "ROST", "OTIS", "PPG", "DAL", "VRSK", "ED", "WEC", "YUM",
    "CPRT", "DHI", "PCG", "FDX", "AMP", "GLW", "CSX", "PEG", "IDXX",
    "ITW", "BDX", "DTE", "KHC", "IQV", "SBAC", "EXR", "AWK", "TRP", "EBAY",
    "LHX", "AEE", "WTW", "SYY", "MTB", "FSLR", "TROW", "CBRE", "ROK", "SRE",
    "CDNS", "WDC", "HAL", "FITB", "HBAN", "EIX", "VTR", "PWR", "STE",
]
DEFAULT_TICKER = TICKERS[0]

# Descarga diaria (cron/tarea programada, ver run_daily_pipeline.bat): NO
# tiene sentido volver a pedir HISTORY_PERIOD (10 años) completo cada día
# para los 210 tickers — es una llamada a yfinance mucho más pesada de lo
# necesario, y cada ejecución escribiría en data/raw/ un CSV nuevo de
# ~2.500 filas por ticker, para siempre, solo para traer 1 fila realmente
# nueva. `download.py --daily` usa esta ventana corta en vez de
# HISTORY_PERIOD. Se deja margen sobre "ayer" (no solo 1-2 días) para
# cubrir fines de semana, festivos de mercado y algún día que la tarea
# programada no llegara a ejecutarse (PC apagado) sin perder ninguna
# sesión — el solape con lo que ya hay en daily_prices es inofensivo
# porque load.py hace upsert por (ticker, date), nunca duplica.
DOWNLOAD_DAILY_LOOKBACK_DAYS = 10

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
#
# Override opcional por variable de entorno (2026-08-27, ver CONTEXTO.md
# "Roadmap v1 (MVP para publicar)"): la demo publicada usa una base de
# datos reducida (data/stocker_demo.db, ~50 tickers en vez de 208, ver
# src/export_demo_db.py) que no cabe en el mismo sitio que la real sin
# pisarla. STOCKER_DB_PATH permite apuntar el despliegue a ese fichero
# sin tocar ni un .bat programado ni ningún script del pipeline local —
# si no está definida, el comportamiento es EXACTAMENTE el de siempre.
DB_PATH = Path(os.environ["STOCKER_DB_PATH"]) if os.environ.get("STOCKER_DB_PATH") else DATA_DIR / "stocker.db"
DB_URL = f"sqlite:///{DB_PATH}"

# --- Calendario de mercado ---
# Usado para distinguir huecos de calendario (fines de semana / festivos
# NYSE-NASDAQ) de fallos reales de descarga. Ver pandas_market_calendars.
MARKET_CALENDAR = "NYSE"

# Minutos de margen tras el cierre oficial antes de considerar definitivo el
# cierre de una sesión (auditoría, C2). yfinance tarda unos minutos en
# consolidar el precio y el volumen finales. Ver src/market_time.py.
MARKET_CLOSE_BUFFER_MINUTES = 20

# --- Datos corporativos: splits y dividendos (auditoría, C3) ---
# `download.py --daily` solo trae los últimos días, pero yfinance reajusta
# TODO el histórico tras un split (Close) o un dividendo (Adj Close). Si al
# cargar se detecta un split/dividendo en la ventana, o si las fechas que se
# solapan con lo ya guardado difieren más de esta tolerancia relativa, se
# vuelve a descargar y a sustituir el histórico completo de ese ticker
# (`load.refresh_full_history`). Caso real: el split 2x1 de Amphenol (APH)
# dejó en daily_prices una caída falsa del -49 % el 26/08/2026.
CORPORATE_ACTION_TOLERANCE = 0.0005

# Un ticker cuya última fecha en daily_prices tenga más de estos días
# naturales de retraso respecto al más reciente del universo se considera
# obsoleto (auditoría, A2): no se predice y el dashboard lo oculta de los
# selectores. Cubre deslistados y descargas que fallan repetidamente.
STALE_TICKER_DAYS = 7

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
# Mismo mecanismo de override que DB_PATH (ver arriba) — la demo publicada
# usa models_demo/ (solo los 3 .joblib elegidos para publicar, un
# subconjunto estable de models/) vía STOCKER_MODELS_DIR. Los modelos NO
# se reentrenan para la demo: no usan el ticker como feature, sirven
# igual sobre el subconjunto de tickers reducido (ver CONTEXTO.md).
MODELS_DIR = Path(os.environ["STOCKER_MODELS_DIR"]) if os.environ.get("STOCKER_MODELS_DIR") else BASE_DIR / "models"

# --- Horizontes de predicción (2026-08-13, ver CONTEXTO.md "Horizontes de
# predicción: semana y mes") ---
# Hasta ahora solo existía target a 1 sesión de mercado (mañana). Se añaden
# 5 sesiones (~1 semana de mercado) y 20 sesiones (~1 mes de mercado —
# ~21 sesiones/mes en realidad, se redondea a 20 por simplicidad y porque
# ya es la ventana usada en ma_20/return_20d/volatility_10d, así no se
# introduce un número nuevo sin precedente en el proyecto). Las claves son
# SESIONES DE MERCADO, no días naturales — mismo criterio que
# target_up_down a 1 día (gold.py: shift() sobre la serie ordenada por
# fecha de cada ticker, nunca "N días naturales después"). El valor es la
# etiqueta corta usada para nombrar model_version (ver model.py) y para
# las etiquetas del selector en el dashboard.
PREDICTION_HORIZONS = {1: "dia", 5: "semana", 20: "mes"}
DEFAULT_PREDICTION_HORIZON = 1

# ¿Usar las features de sentimiento de noticias en el modelo? (auditoría,
# A3). Desactivado: la investigación de CONTEXTO.md no encontró señal, en
# los ~9,5 años de entrenamiento valen 0 casi siempre (las noticias solo
# cubren desde febrero de 2026) y en inferencia la cobertura diaria era de
# ~20 tickers de 208, así que el modelo veía "neutro" donde en realidad no
# había datos. Las columnas se siguen calculando en gold_*; solo cambia qué
# columnas recibe el modelo. Los modelos ya entrenados guardan su propia
# lista (`feature_names`) y siguen funcionando igual.
MODEL_USE_NEWS_FEATURES = False

# Tamaño de la muestra del test usada para la importancia por permutación
# (auditoría, M2). La importancia MDI de Random Forest favorece variables
# continuas; la permutación mide cuánto empeora el acierto en el test al
# desordenar cada variable. 50.000 filas x 3 repeticiones mantiene el coste
# en unos minutos.
PERMUTATION_IMPORTANCE_SAMPLE = 50_000
PERMUTATION_IMPORTANCE_REPEATS = 3

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

# CORREGIDO 2026-08-06 — bug de diseño real, no de cupo (ver CONTEXTO.md,
# "Rediseño de noticias: 1 ticker por llamada"). Se agrupaban
# NEWS_TICKERS_PER_CALL tickers por llamada (comma-separated en el
# parámetro `tickers` de la API) asumiendo semántica OR ("noticias de
# cualquiera de estos tickers"). Evidencia real: de los 27 ficheros JSON
# guardados hasta la fecha (distintos lotes de 10 mega-caps, distintas
# ventanas desde 2024-08), TODOS devolvieron "feed": [] — cero artículos,
# algo estadísticamente imposible para un mes de noticias de NVDA/AAPL/
# MSFT/AMZN/... si el filtro fuera OR. La hipótesis mejor respaldada es
# que `tickers` usa semántica AND (el artículo debe mencionar TODOS los
# tickers del lote a la vez), lo que hace casi imposible que un lote de 10
# tickers no relacionados devuelva nada. No confirmado en la documentación
# oficial (contenido no accesible vía fetch, cargado por JS), pero la
# evidencia empírica es contundente (0/27). Cupo demasiado escaso para
# gastarlo en más pruebas: se corrige directamente a 1 ticker por llamada,
# que es la única composición que garantiza intersección no vacía.
NEWS_TICKERS_PER_CALL = 1

# Backfill inicial: 6 meses (decisión explícita del usuario, 2026-08-06,
# suficiente para el propósito del proyecto y mucho más rápido de
# conseguir tras el fix de arriba). NEWS_BACKFILL_WINDOW_DAYS = 180 cubre
# los 6 meses en una sola ventana por ticker, así que con
# NEWS_TICKERS_PER_CALL = 1 el backfill completo son exactamente 208
# llamadas (una por ticker) ≈ 9 días de ejecuciones a
# NEWS_DAILY_CALL_BUDGET/día — más llamadas totales que antes por ticker,
# pero cada una con datos reales en vez de 0 artículos garantizados, y en
# menos tiempo que el plan anterior (~21 días) porque ya no hay lotes de
# 10 ventanas mensuales por ticker, solo 1. Riesgo aceptado: `limit=1000`
# de la API por llamada podría truncar tickers muy mediáticos (TSLA,
# NVDA) si superan 1000 artículos en 6 meses — con sort=RELEVANCE se
# quedarían los más relevantes según el propio scoring de Alpha Vantage,
# no un recorte cronológico arbitrario.
# El progreso se guarda en la tabla news_backfill_progress para poder
# parar y retomar entre ejecuciones (ver src/news.py). Los lotes antiguos
# (de 10 tickers) quedan huérfanos de forma natural: `_batch_key()` es un
# hash de la composición exacta del lote, así que un lote de 1 ticker
# nunca coincide con la clave de un lote de 10 — no hace falta migrar ni
# borrar nada a mano.
NEWS_BACKFILL_MONTHS = 6
NEWS_BACKFILL_WINDOW_DAYS = 180

# BUG REAL CORREGIDO (2026-08-08) — ver CONTEXTO.md "Bug de no-convergencia
# del backfill". `date_windows()` (news.py) calculaba sus ventanas con
# `end = dt.date.today()` por defecto: como "hoy" avanza un día cada vez
# que se ejecuta el cron, TODAS las ventanas (no solo la más reciente) se
# recalculaban con fechas distintas cada día — el `window_start` de ayer
# nunca coincidía con el de hoy, así que `news_backfill_progress` (clave
# compuesta batch_key + window_start) nunca reconocía nada como "ya
# hecho": el backfill volvía a "208/208 pendientes" cada ejecución, sin
# converger nunca, sin importar cuántos días se dejara corriendo. Esto
# llevaba presente desde el diseño original de 24 meses/ventanas de 30
# días (se veía como el contador de ventanas subiendo en vez de bajar
# entre ejecuciones, ya observado en el log del 2026-08-06 y mal
# atribuido entonces solo a confusión de cupo). Fix: las ventanas del
# backfill se anclan a esta fecha FIJA, no a `dt.date.today()` — así
# `window_start` es estable entre ejecuciones y el progreso si se
# acumula. No se actualiza sola: es un ancla deliberada del backfill en
# curso, no una fecha "viva". `run_daily_update()` no se ve afectado (no
# usa `date_windows()`, calcula ayer/hoy directamente cada vez, donde sí
# tiene sentido que sea relativo a "hoy").
NEWS_BACKFILL_REFERENCE_DATE = dt.date(2026, 8, 8)

# --- Prioridad de tickers en la actualización diaria (2026-08-25, ver
# CONTEXTO.md "Prioridad de noticias para tickers relevantes") ---
# Petición del usuario: que un subconjunto de "las acciones más relevantes
# del mercado" se actualice más a menudo que el resto en
# `news.run_daily_update()`, en vez de quedar sujeto solo al muestreo
# aleatorio uniforme sobre los 208 tickers (~cada 8-9 días de media).
#
# No hay ningún campo de capitalización bursátil real en la base de datos
# (stocks) para ordenar "de verdad" por tamaño de mercado — en vez de
# introducir una fuente de datos nueva solo para esto, se reutiliza el
# orden ya existente de TICKERS (arriba), que el propio usuario ya dejó
# empezando por los mega-caps: NVDA, AAPL, MSFT, AMZN, GOOGL, GOOG, AVGO,
# META, TSLA, LLY... Se toman los primeros NEWS_PRIORITY_TICKERS_COUNT
# como proxy de "relevancia de mercado". 50 es un punto intermedio del
# rango pedido (25-100) — ajustar aquí si se quiere un set más grande o
# más pequeño.
NEWS_PRIORITY_TICKERS_COUNT = 50
NEWS_PRIORITY_TICKERS = TICKERS[:NEWS_PRIORITY_TICKERS_COUNT]

# Fracción de NEWS_DAILY_CALL_BUDGET reservada cada día para rotar por
# NEWS_PRIORITY_TICKERS (ver news.run_daily_update — ronda determinista,
# no aleatoria, para no repetir un ticker antes de haber pasado por todos
# los demás). El resto del presupuesto sigue el muestreo aleatorio de
# siempre, pero solo sobre los tickers NO prioritarios.
#
# Trade-off real y aceptado: el presupuesto total sigue siendo
# NEWS_DAILY_CALL_BUDGET (25, límite del free tier de Alpha Vantage) — no
# hay llamadas "extra" para los prioritarios, se les da más peso DENTRO
# del mismo presupuesto. Con los valores por defecto (50 prioritarios,
# 80% del presupuesto = 20 llamadas/día), los prioritarios rotan en
# ceil(50/20) = 3 días; a cambio, los 158 restantes se reparten solo 5
# llamadas/día en vez de las ~25 de antes, así que su frecuencia media de
# refresco empeora de ~8-9 días a ~32 días. Se acepta porque el usuario
# pidió explícitamente priorizar relevancia sobre cobertura uniforme.
NEWS_PRIORITY_DAILY_SHARE = 0.8

# Ventana máxima (días) por llamada en la actualización diaria (auditoría,
# A3). Cada ticker se pide desde su última fecha consultada hasta hoy, pero
# si lleva mucho sin consultarse se limita a esta ventana para no chocar
# con el máximo de 1.000 artículos por llamada de Alpha Vantage. Con 25
# llamadas/día y un 80 % para los prioritarios, los 157 tickers restantes
# se consultan cada ~32 días: 35 días cubre esa vuelta sin huecos.
NEWS_DAILY_MAX_WINDOW_DAYS = 35

NEWS_RAW_DIR = DATA_DIR / "raw" / "news"

# --- Logos de empresa (2026-08-28) ---
# Petición del usuario: mostrar el logo de cada empresa en el dashboard
# para hacerlo más visual. Clearbit Logo API (la opción obvia hace un
# tiempo) cerró en diciembre 2025 — comprobado antes de elegir nada, ver
# CONTEXTO.md "Logos de empresa en el dashboard". Se eligió Logo.dev
# (sucesor oficial de Clearbit) sobre la alternativa sin cuenta
# (AllInvestView Ticker Logos, gratis pero exige un enlace de atribución
# visible en el dashboard) — decisión explícita del usuario: prefiere
# crear una cuenta gratuita antes que mostrar una atribución de terceros
# en un proyecto de máster.
#
# LOGO_DEV_TOKEN es una "publishable key" (pensada para ir en la URL de
# una <img>, no es un secreto que haya que esconder del cliente) — aun
# así se lee de entorno/.env, nunca hardcodeada, por si cambia de cuenta
# o de plan. Sin ella configurada, el dashboard simplemente no muestra
# logos (no rompe nada) — ver `dashboard/data_access.ticker_logo_url()`.
# Consíguela gratis (sin tarjeta) en https://www.logo.dev/signup.
LOGO_DEV_TOKEN = os.environ.get("LOGO_DEV_TOKEN", "")

# --- Chat de "Contenido Premium" (2026-08-31) ---
# Petición del usuario: un chat experto por hyperscaler para resolver
# dudas sobre sus resultados, usando como contexto los documentos
# oficiales de cada trimestre y el análisis ya redactado (nunca la
# transcripción del vídeo de terceros — ver CONTEXTO.md, "Chat experto
# sobre earnings: viabilidad y diseño"). A diferencia de LOGO_DEV_TOKEN,
# esta SÍ es una clave secreta de servidor (API key de Anthropic, con
# coste por token) — nunca debe ir a un <img> ni exponerse al cliente.
# Sin ella configurada, `dashboard/premium_chat.py` deja el chat
# deshabilitado con un aviso, no rompe el resto del dashboard.
# Clave en https://console.anthropic.com/ (requiere cuenta con billing).
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Opcional — SOLO hace falta si tu ANTHROPIC_API_KEY es una "personal key"
# o "service account key" sin workspace fijado al crearla (Claude Console
# > Settings > API keys > Linked account: tú, sin marcar un workspace
# concreto). Esas claves son "identity-linked" y la API exige indicar en
# qué workspace opera cada petición vía la cabecera
# `anthropic-workspace-id` — si no se manda, la API devuelve el error 400
# "anthropic-workspace-id is required when authenticating with an
# identity-linked API key". Se encuentra en Claude Console > Settings >
# Workspaces > columna "ID" (empieza por `wrkspc_`). Si tu clave SÍ está
# ligada a un workspace concreto (o es una "workspace key" clásica), deja
# esto vacío — no hace falta y así te ahorras tener que mantenerlo.
ANTHROPIC_WORKSPACE_ID = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")

# Modelo barato y rápido a propósito: esto es preguntas y respuestas
# ancladas a unos pocos documentos concretos, no una tarea que necesite
# el modelo más potente — ver CONTEXTO.md sobre el control de coste de
# esta función, expuesta sin login todavía.
PREMIUM_CHAT_MODEL = "claude-haiku-4-5-20251001"

# Límite de preguntas por sesión de navegador (contador en
# st.session_state, se reinicia si el usuario recarga la página) y límite
# global por día (contador en un fichero local aparte, ver
# `dashboard/premium_chat.py` — única excepción documentada a la regla de
# "el dashboard nunca escribe nada", y solo para este fichero de conteo,
# nunca para stocker.db). Ambos existen porque el chat SÍ tiene coste por
# pregunta, a diferencia del resto del dashboard, y la página de
# Contenido Premium es pública sin autenticación todavía.
PREMIUM_CHAT_MAX_PER_SESSION = 10
PREMIUM_CHAT_MAX_PER_DAY_GLOBAL = 150

# Límite diario por dirección IP (auditoría, A5): el límite por sesión se
# salta recargando la página. Streamlit expone la IP del visitante en
# `st.context.ip_address` cuando la app está desplegada; en local vale None
# y solo aplican los otros dos límites.
PREMIUM_CHAT_MAX_PER_DAY_PER_IP = 20

# Documentos que NO entran en el contexto del chat (auditoría, A5). Los
# 10-Q completos pesan cientos de KB (el de META, 460 KB) y llevaban el
# corpus de META a ~150-190 mil tokens, cerca del límite de contexto de
# Haiku 4.5 antes incluso del historial. El comunicado, la transcripción
# y el análisis ya cubren lo que se pregunta en la práctica.
PREMIUM_CHAT_EXCLUDE_PATTERNS = ["10q_"]

# Tope de tamaño del corpus en caracteres (~4 caracteres por token). Si se
# supera, se descartan primero los documentos oficiales más grandes; el
# análisis redactado se conserva siempre.
PREMIUM_CHAT_MAX_CORPUS_CHARS = 400_000

# Mensajes del historial que se envían en cada pregunta (los más recientes).
PREMIUM_CHAT_MAX_HISTORY_MESSAGES = 10
