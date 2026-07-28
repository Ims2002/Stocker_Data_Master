# src/

Código del pipeline de Stocker, según el diseño ya cerrado en
[`CONTEXTO.md`](../CONTEXTO.md). Implementados hasta la capa processed
(config, descarga, esquema y carga); features/gold/modelo/predicción aún
no — esto describe qué contendrá cada módulo.

## Módulos previstos

- **`config.py`** (implementado) — configuración central: ticker(s), rango de histórico, rutas de datos y de la base de datos. Ningún otro módulo debe hardcodear estos valores; deben importarse desde aquí.
- **`download.py`** (implementado) — descarga el histórico diario vía `yfinance` (fuente única, ver CONTEXTO.md) y lo vuelca tal cual en `data/raw/` (CSV sin transformar, `auto_adjust=False` para conservar `Close` y `Adj Close` por separado), como copia de auditoría de qué se descargó y cuándo. Pensado para lotes de varios tickers: cada uno se descarga de forma aislada, con reintentos (`config.DOWNLOAD_RETRY_ATTEMPTS`) y una pausa entre tickers (`config.DOWNLOAD_DELAY_SECONDS`) para mitigar rate-limiting; un fallo en un ticker no aborta el resto del lote. Fallback documentado (no activo) en `fallback/yahooquery_download.py` si `yfinance` deja de funcionar.
- **`db.py`** (implementado) — esquema de las 5 tablas (`stocks`, `daily_prices`, `gold_train`, `gold_inference`, `predictions`) vía SQLAlchemy Core, con las FK y CHECK constraints que refuerzan las reglas de CONTEXTO.md (p. ej. `gold_inference` no tiene columnas de label ni siquiera en el esquema). `gold_train`/`gold_inference` son compartidas por todos los tickers (antes se llamaban `gold_aapl_train`/`gold_aapl_inference`, renombradas al ampliar de un solo ticker a un universo multi-ticker). `init_db()` crea las tablas en `config.DB_PATH` si no existen; `seed_stocks()` inserta una fila en `stocks` por ticker (por defecto los de `config.TICKERS`, pero acepta una lista explícita); `drop_legacy_gold_tables()` es una migración puntual para limpiar las tablas con el nombre antiguo si quedaron de una ejecución previa. Motor: SQLite (decisión confirmada — ver CONTEXTO.md; no MySQL).
- **`load.py`** (implementado) — lee el CSV raw más reciente de `data/raw/` (uno por ticker) y hace upsert en `daily_prices`: tipado correcto, fechas ISO sin componente horaria, descarte de registros inválidos (Open/Close nulos o ≤0, Volume nulo o negativo) con log de calidad en `data/processed/quality_log.csv`, y validación de huecos contra el calendario de mercado (`pandas_market_calendars`, `config.MARKET_CALENDAR`) — avisa de sesiones de mercado esperadas sin datos (posible fallo de descarga), sin rellenarlas ni descartarlas. Siembra el ticker concreto en `stocks` antes de cargarlo (no depende de que ya esté en `config.TICKERS`). En lote, cada ticker se procesa de forma aislada: un fallo no aborta el resto. No llama a la API directamente: solo consume lo que `download.py` ya volcó en `data/raw/`, para no saltarse la copia de auditoría del pipeline.
- **`features.py`** (pendiente) — cálculo de `return_1d`, `ma_5`, `ma_10`, `ma_20`, `volatility_10d` sobre `adj_close` (no `close`), usando solo datos de días ≤ t (sin lookahead), por ticker.
- **`gold.py`** (pendiente) — construcción física y separada de `gold_train` (incluye labels, excluye por construcción, por ticker, la última fecha disponible) y `gold_inference` (solo la última fecha de cada ticker, sin ninguna columna de label).
- **`model.py`** (pendiente) — entrenamiento y evaluación con split temporal (nunca aleatorio) frente a los baselines obligatorios: clase mayoritaria y persistencia del día anterior.
- **`predict.py`** (pendiente) — inferencia sobre `gold_aapl_inference` y persistencia obligatoria del resultado en la tabla `predictions`, junto con `model_version`.

## Reglas ya decididas que no deben reinterpretarse

Ver `CONTEXTO.md` para el diseño completo. En particular:

- Ningún módulo debe recomponer `gold_train` y `gold_inference` en una única tabla "por comodidad" — están separadas físicamente a propósito para que el data leakage sea estructuralmente imposible, no solo una cuestión de disciplina en el código.
- Ninguna ruta, ticker o rango de fechas debe hardcodearse fuera de `config.py`. La lista de tickers la mantiene el usuario a mano en `config.TICKERS` — no se scrapea de ningún sitio (no hay fuente oficial gratuita de composición de índices).
- Al agregar OHLC, `Open` = primer valor, `High` = máximo, `Low` = mínimo, `Close` = último valor, `Volume` = suma — nunca `.mean()`.
- Cualquier split train/test sobre `gold_train` es cronológico, nunca aleatorio, y no debe mezclar fechas de distintos tickers de forma que se filtre información entre ellos.
- En descargas/cargas por lotes, un fallo en un ticker no debe abortar el resto — cada ticker se procesa de forma aislada (ver `download.py`/`load.py`).
