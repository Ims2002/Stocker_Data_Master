# Stocker — Contexto de proyecto (brief para desarrollo)

## Qué es el proyecto

Sistema propio de histórico y predicción diaria del precio de una acción. Arrancó como MVP de una única acción (**AAPL**) y se ha ampliado a un universo multi-ticker: **210 acciones de EE.UU.** (`config.TICKERS`). El objetivo no es predecir el precio exacto de cierre, sino clasificar si el precio **subirá o bajará al día siguiente**, dando a un inversor particular una señal orientativa diaria basada en datos históricos, sin depender de plataformas de pago.

Alcance actual: varias acciones (no ya una única), datos diarios (no intradía), y un modelo de clasificación binaria (por ahora, uno por ticker — ver `gold_train`/`gold_inference` más abajo). Quedan fuera del alcance: fuentes externas (noticias, sentimiento, datos macro) — se contemplan como ampliaciones futuras.

**Universo de tickers**: no existe una API oficial y gratuita para la composición de índices (S&P 500 u otros; yfinance no la aporta), así que la lista de tickers la define y mantiene el usuario directamente en `config.py` — no se scrapea de Wikipedia ni de ningún otro origen no documentado. El usuario pasó una lista inicial de ~250 EE.UU. + ~43 España (IBEX/BME); tras deduplicar (había 18 repetidos) y corregir el formato de una clase de acciones (`BRK.B` → `BRK-B`), se descartaron 10 tickers ambiguos o no verificables en vez de adivinarlos (mezcla de erratas, ADRs extranjeros que no son del S&P 500, y un ticker de ETF) — criterio explícito del usuario: solo entran tickers de los que se tenga certeza. Los 43 de España quedan aparcados por ahora: `pandas_market_calendars` no tiene ningún calendario de Madrid/BME/IBEX, así que el chequeo de huecos de mercado (ver Reglas de limpieza) no se puede aplicar de forma fiable todavía — se retomará cuando se resuelva ese punto.

**Contexto de decisión**: se evaluaron dos propuestas (Stocker y una optimización de rutas de última milla sobre un dataset de Amazon). El profesor del curso recomendó la de Amazon por estar más alineada con el máster, pero exigió elegir una única idea. Se decidió mantener **Stocker**, aplicando el rigor de evaluación que pidió el profesor (ver más abajo). La propuesta de Amazon queda descartada.

## Fuente y pipeline de datos

- **Fuente**: API de Yahoo Finance, accedida vía la librería Python `yfinance`. Gratuita, sin API key, pero **no es una API oficial** (cliente que consume endpoints internos de Yahoo) — riesgo de rotura si Yahoo cambia su web.
- **Actualización**: descarga diaria automatizada (cron/scheduled job), respetando la zona horaria del mercado (ET, NYSE/NASDAQ).
- **Alternativas si `yfinance` falla**: Alpha Vantage (API oficial, key gratuita), Stooq (CSV directo), datasets históricos en Kaggle.
- **Pipeline de almacenamiento**: API → CSV crudo (raw, sin transformar) → SQL (processed) → capa gold (ver esquema abajo).
- **Motor SQL**: SQLite en el MVP; migración natural a PostgreSQL en el VPS de Hetzner del usuario si escala a más tickers.

## Esquema de datos — DISEÑO FINAL (post-feedback del profesor)

La capa gold **no es una única tabla**: se divide físicamente en tres artefactos para que sea estructuralmente imposible que el target se cuele como feature.

```
stocks
├── ticker (PK), nombre, sector (opcional), pais (opcional — para poder filtrar tickers por país cuando se amplíe a más acciones)

daily_prices   (processed — histórico limpio y tipado)
├── ticker (FK), date, open, high, low, close, adj_close, volume
└── PRIMARY KEY (ticker, date)

gold_train   (gold — SOLO para entrenar/evaluar; compartida entre tickers)
├── ticker, date (PK)
├── FEATURES: open, high, low, close, adj_close, volume,
│             return_1d, ma_5, ma_10, ma_20, volatility_10d
│             (calculadas solo con datos ≤ día t, sin lookahead, por ticker)
├── LABELS: close_next_day, target_up_down (0/1)
└── Excluye por construcción, por cada ticker, su última fecha disponible (target aún no existe)

gold_inference   (gold — SOLO para predecir "mañana"; compartida entre tickers)
├── ticker, date (PK) — una única fila POR TICKER: su día más reciente
├── Mismas FEATURES que gold_train
└── NO contiene close_next_day ni target_up_down (ni siquiera vacías)

predictions   (OBLIGATORIA, no opcional)
├── ticker, date_predicha, predicted_target_up_down, predicted_probability,
│   model_version, predicted_at, actual_target_up_down (se rellena a posteriori)
└── PRIMARY KEY (ticker, date_predicha, model_version)
```

- `daily_prices` 1:1 `gold_train` (todas las fechas salvo la última, por ticker) y 1:1 `gold_inference` (solo la última fecha, por ticker).
- `gold_inference` 1:N `predictions` (N si se versionan varios modelos, por ticker).
- `predictions` se cruza después con `daily_prices` para rellenar `actual_target_up_down` una vez se conoce el cierre real, y así medir el rendimiento real del modelo en producción, no solo en backtest.
- Volumen esperado: ~1.250 filas (5 años) a ~2.500 filas (10 años) de histórico diario **por ticker**; con 210 tickers, del orden de 260.000-525.000 filas en `daily_prices` — sigue siendo manejable en SQLite, aunque más cerca del rango en el que replantearse PostgreSQL (ver "Riesgos conocidos").

## Variable objetivo

Se construye desplazando el `Close` un día hacia atrás (`shift(-1)`) y comparándolo con el `Close` actual → `target_up_down` (1 = sube, 0 = baja/igual). **Es un problema de clasificación binaria, no de regresión del precio exacto.**

## Contrato de evaluación (cierre explícito pedido por el profesor)

- **División temporal, nunca aleatoria**: split cronológico (ej. últimos 6-12 meses como test) por la autocorrelación de series temporales; un `train_test_split` aleatorio filtraría información del futuro al pasado. Mejora deseable sobre el MVP: validación walk-forward (ventana expansiva).
- **Baselines obligatorios**: comparar siempre contra (1) predecir la clase mayoritaria y (2) repetir la tendencia del día anterior. El modelo solo es útil si los supera de forma consistente en el test temporal.
- **Sin leakage por diseño**: `gold_inference` no tiene columnas de label; las features nunca usan datos futuros (ventanas solo hacia atrás, calculadas de forma independiente por ticker).
- **Persistencia obligatoria**: cada predicción real (no solo de backtest) se guarda en `predictions` con la versión del modelo, para comparar rendimiento real vs. backtest y detectar degradación.
- **Honestidad de resultado**: si el modelo no supera al baseline, es un resultado válido que debe reportarse, no ocultarse — el mercado a corto plazo se comporta cerca de un paseo aleatorio, así que ese es un desenlace realista, no un fracaso del proyecto.

## Errores ya detectados en el prototipo (notebook inicial) — corregir al implementar

1. **Data leakage**: en una versión previa, `X` incluía la propia columna del target (`stonks?`) como feature — excluir explícitamente la columna objetivo de `X` con `.drop()` (o, mejor, usar `gold_inference`, que ni siquiera tiene esa columna).
2. **Agregación diaria incorrecta**: al agregar datos intradía a diario se usó `.mean()` para Open/High/Low/Close. Correcto: `Open`=primer valor, `High`=máximo, `Low`=mínimo, `Close`=último valor, `Volume`=suma.
3. **Split train/test invertido**: se usó `train_size=0.3` (30% train / 70% test); además, ese split era aleatorio y ahora debe ser temporal (ver contrato de evaluación).

## Reglas de limpieza y calidad ya decididas

- No rellenar huecos de fin de semana/festivos de mercado (usar calendario de mercado, ej. `pandas_market_calendars`, para distinguirlos de fallos reales de descarga).
- Usar `Adj Close`, no `Close`, para retornos y medias móviles (splits/dividendos).
- Duplicados resueltos vía upsert por `(ticker, date)`.
- Fechas ISO (`YYYY-MM-DD`), sin componente horaria.
- Outliers de alta volatilidad (earnings, anuncios) son eventos reales — no filtrar automáticamente.
- Registros inválidos (Open/Close nulos o negativos, Volume negativo) se descartan y quedan en un log de calidad.
- No se descarta ninguna columna de la API; `High`/`Low` se conservan aunque no se usen como feature en la primera versión del modelo.

## Riesgos conocidos

- Dependencia de `yfinance` (no oficial) para la actualización diaria automática.
- Complejidad de distinguir huecos esperados vs. fallos reales de descarga.
- Capacidad predictiva real limitada por la naturaleza casi aleatoria de los precios a corto plazo — el listón de éxito es superar un baseline ingenuo, no acertar el precio exacto.
- Si SQL da más fricción de la esperada, fallback a mantener las mismas capas sobre CSV puro (sacrificando upsert y consultas transversales entre acciones).
- Riesgo específico de este diseño: si en el pipeline se recompone `gold_train` + `gold_inference` en una sola tabla "por comodidad", se reintroduce el riesgo de leakage que este diseño elimina a propósito.
- **Límite de peticiones con 210 tickers**: descargar secuencialmente muchos tickers vía `yfinance` aumenta el riesgo de rate-limiting o bloqueos temporales de Yahoo Finance; el pipeline de descarga trata cada ticker de forma aislada, con reintentos y pausa entre tickers (`config.DOWNLOAD_*`), y no asume que todos se descargarán siempre con éxito en una misma ejecución.
- **Migración a PostgreSQL**: con 210 tickers el volumen (~260.000-525.000 filas en `daily_prices`) sigue siendo manejable en SQLite, pero ya no es tan holgado como con 1 solo ticker; si se amplía más (p. ej. al añadir España u otros mercados), replantearse la migración ya prevista a PostgreSQL en el VPS de Hetzner.
- **Calendario de mercado no disponible para todos los mercados**: `pandas_market_calendars` no tiene calendario de Madrid/BME/IBEX (sí NYSE, LSE, EUREX...), por lo que el chequeo de huecos de mercado solo es fiable para tickers de EE.UU. por ahora — los ~43 tickers españoles suministrados por el usuario quedan fuera del universo activo hasta resolver este punto.

## Infraestructura disponible

VPS de Hetzner del usuario (usado actualmente para servidores de juego tipo Palworld/Minecraft), candidato para alojar el job de actualización diaria y, si se escala a PostgreSQL, la propia base de datos.
