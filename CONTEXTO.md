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

## Enfoque de modelado (decisión de implementación, post-ampliación a multi-ticker)

Al implementar `model.py` con el universo de 210 tickers ya cargado, se tomaron dos decisiones de diseño que no estaban cerradas en las entregas del curso (que asumían un único ticker):

- **Modelo agrupado (pooled), no un modelo por ticker**: se entrena un único clasificador sobre `gold_train` de los 210 tickers a la vez, en vez de 210 modelos aislados con ~2.500 filas cada uno. Razón: la muestra efectiva por ticker es baja (autocorrelación, pocos regímenes de mercado distintos en 10 años — ver discusión de viabilidad), y agrupar da ~500.000 filas de potencia estadística real para una señal ya de por sí débil. El ticker NO se usa como feature (ni one-hot ni embedding) en esta primera versión, para no mezclar "aprender la idiosincrasia de una acción" con "aprender una señal técnica general" — queda como mejora natural pendiente (embeddings de ticker, agrupación sectorial).
- **Features del modelo en escala relativa, no en niveles de precio crudos**: aunque `gold_train`/`gold_inference` almacenan `open/close/adj_close/volume` tal cual (ya cerrado en el esquema), el modelo NO los usa directamente como entrada — con un panel de 210 tickers de precios muy distintos (una acción a 20$ y otra a 900$), el nivel absoluto de precio no es comparable entre filas. En su lugar, `model.py` construye su propia matriz de features a partir de las columnas ya almacenadas: `return_1d`, `volatility_10d`, `close` respecto a sus medias móviles (`close/ma_N - 1`, no el nivel de la media), y `log1p(volume)`. Esto no cambia el esquema de `gold_train`/`gold_inference` (sigue guardando los niveles crudos, por si se necesitan para otra cosa), solo qué subconjunto/transformación de esas columnas ve el modelo.
- **Modelos interpretables por defecto (Random Forest / regresión logística), no LSTM**: con ~2.500 filas por ticker (aun agrupando, cada ticker sigue aportando pocas observaciones a su propia serie temporal), un LSTM es más propenso a sobreajustar y es una caja negra — contrario al objetivo explícito de que el dashboard sea claro e interpretable. Un modelo LSTM queda como opción "avanzada" a futuro, no como modelo por defecto.

## Noticias y sentimiento (decisión de fuente, 2026-07-30)

Fuente NUEVA respecto al resto del pipeline: `yfinance` cubre precios, pero no noticias con sentimiento ya puntuado a la profundidad histórica que necesita este proyecto. Se compararon tres opciones antes de decidir:

- **Alpha Vantage `NEWS_SENTIMENT` (ELEGIDA)**: da un score de sentimiento ya calculado por su propio NLP (bullish/neutral/bearish + relevancia por ticker), y admite agrupar varios tickers por llamada (`tickers=AAPL,MSFT,...`). Contra: el free tier son 25 peticiones/día **en total** (no solo para este endpoint), y el `limit` de artículos por llamada (máx. 1000) se reparte entre todos los tickers pedidos en esa llamada — con lotes grandes, los tickers menos mediáticos pueden quedarse con poca o ninguna cobertura en una ventana de fechas concreta. La profundidad histórica de sentimiento tampoco llega a los 10 años de precios.
- Finnhub `company-news`: límite mucho más generoso (60/min), pero su endpoint de sentimiento pasó a ser de pago — el free tier solo da titulares en crudo, habría que puntuar el sentimiento con un modelo propio (VADER/FinBERT).
- `yfinance.news`: sin dependencia nueva, pero solo trae titulares muy recientes (no sirve para reconstruir histórico) y tampoco puntúa sentimiento.

**Diseño implementado (`src/news.py`, `src/db.py`)**:

- Los 208 tickers de `config.TICKERS` se agrupan en lotes de `NEWS_TICKERS_PER_CALL` (50 por defecto → 5 lotes) por llamada.
- El backfill histórico pedido (~2 años, decisión explícita del usuario) se trocea en ventanas de `NEWS_BACKFILL_WINDOW_DAYS` días (30 por defecto) para no agotar el `limit` de artículos de golpe: 5 lotes x ~24 ventanas mensuales ≈ 120 llamadas ≈ **~5 días** de ejecuciones a 25 llamadas/día.
- El progreso (qué combinación lote×ventana ya se pidió) se persiste en la tabla `news_backfill_progress`, para poder ejecutar `python news.py --backfill` varios días seguidos sin repetir trabajo ni desperdiciar presupuesto de peticiones.
- Capas iguales al resto del pipeline: raw (`data/raw/news/*.json`, auditoría) → processed (tabla `news_articles`, PK `(ticker, url)`, upsert = dedupe natural si dos ventanas se solapan) → agregado (**vista** `news_sentiment_daily`, no tabla física — se recalcula sola agrupando `news_articles` por `(ticker, date)`, para que nunca pueda quedar desincronizada, mismo principio que impide fusionar `gold_train`/`gold_inference`).
- Una vez completado el backfill, `python news.py --daily` hace la actualización incremental (ayer→hoy, todos los lotes — solo ~5 llamadas, muy por debajo del presupuesto diario).
- **Pendiente, explícitamente fuera de alcance de este paso**: integrar `news_sentiment_daily` como feature del modelo (`gold.py`/`model.py`). Este módulo solo puebla la base de datos; el usuario pidió tener la información disponible antes de decidir cómo (y si) se incorpora al modelo.
- Requiere `ALPHA_VANTAGE_API_KEY` en un `.env` local (ver `.env.example`, ya en `.gitignore`) — clave gratuita en <https://www.alphavantage.co/support/#api-key>.

**Corrección tras la primera ejecución real contra la API** (2026-07-30): el primer lote falló con `"Information": "Invalid inputs..."`. Dos problemas reales, no solo uno: (1) el código trataba CUALQUIER respuesta con clave `Note`/`Information` como cupo diario agotado y paraba toda la ejecución, cuando esto era un error de parámetros de un solo lote; (2) al no marcarse ese lote como completado, cada ejecución futura habría vuelto a gastar su primera llamada repitiendo el mismo fallo indefinidamente, bloqueando el resto del backfill. Arreglado: `AlphaVantageQuotaError` (para en toda la ejecución) vs `AlphaVantageRequestError` (falla solo ese lote+ventana, el resto sigue) son ahora excepciones distintas.

**Segunda corrección, hipótesis inicial descartada** (mismo día, segunda ejecución real): la primera hipótesis fue que `BRK-B` (formato Yahoo) no lo reconocía Alpha Vantage, que esperaría `BRK.B`. Se añadió `TICKER_SYMBOL_OVERRIDES` para traducirlo (se mantiene, es inofensivo y puede que sí importe en algún caso), pero la segunda ejecución real la **descartó como causa principal**: los lotes 1, 2 y 3 (que NO contienen `BRK-B`) fallaron exactamente igual que el lote 0, y el único lote que funcionó fue el último (8 tickers, el resto de dividir 208/50). Evidencia directa de que el problema es el **número de tickers por llamada**, no el formato de ninguno en concreto — Alpha Vantage no documenta un límite explícito para el parámetro `tickers` de `NEWS_SENTIMENT`, pero empíricamente 50 falla siempre y 8 funciona. Se bajó `NEWS_TICKERS_PER_CALL` a 10 (valor conservador, sin confirmar aún el límite exacto entre 8 y 50 — subirlo costaría llamadas reales del cupo diario en tanteo). Esto cambia la estimación del backfill de ~5 días a **~21 días** (21 lotes x ~24 ventanas ≈ 504 llamadas a 25/día) — ver `config.py` para el detalle actualizado.

**Decisión: automatizar la ejecución diaria en vez de recortar los 2 años de backfill** (2026-07-30). Ante el plazo de ~21 días, la alternativa habría sido reducir la profundidad histórica pedida — se descartó porque no hace falta: `news.py` ahora tiene un modo automático por defecto (sin flags) que decide solo si toca seguir con el backfill o ya pasar al mantenimiento diario, así que el mismo comando programado sirve durante las ~3 semanas del backfill y para siempre después. Se añadió `run_news_daily.bat` en la raíz del repo (usa rutas relativas a su propia ubicación, funciona sin importar el directorio de trabajo desde el que lo lance el programador de tareas) que llama a `python src\news.py` y vuelca la salida en `data\news_cron.log` (gitignored). El usuario debe programarlo una vez al día con el Programador de tareas de Windows — ver `src/README.md` para el comando exacto.

**Incidente real y corrección (2026-07-31)**: al revisar `data/news_cron.log` tras un par de ejecuciones reales, aparecieron dos problemas:

1. **Mojibake en el log** (tildes/rayas ilegibles): el codepage de la consola que usa el Task Scheduler para ejecutar el `.bat` no era UTF-8, y Python heredaba ese codepage para stdout/stderr redirigidos a fichero. Arreglado añadiendo `set PYTHONUTF8=1` en `run_news_daily.bat` antes de invocar `python`.
2. **Progreso de backfill inconsistente al bajar `NEWS_TICKERS_PER_CALL` de 50 a 10** (el cambio del punto anterior de esta misma sección): `news_backfill_progress` usaba `batch_id` (la posición del lote, 0/1/2...) como identidad de "ya hecho". Al cambiar el tamaño de lote, el `batch_id=4` dejó de significar "los últimos 8 tickers" y pasó a significar "los tickers 40-49" — un conjunto totalmente distinto — pero las filas de progreso ya guardadas bajo el `batch_id` antiguo seguían leyéndose como "hecho" para ese número de lote, así que esos tickers nuevos se habrían saltado silenciosamente para siempre sin ningún aviso. Corregido: la identidad ahora es `batch_key` (hash de los tickers exactos del lote, ver `news._batch_key()`), no la posición — si la composición de un lote cambia, genera una clave distinta y no hereda progreso ajeno. `db.py` migra sola cualquier `news_backfill_progress` con el esquema antiguo (la elimina y la recrea vacía la primera vez que se detecta — perder esas filas es seguro, como mucho se repiten unas pocas llamadas ya hechas, nunca se corrompe nada gracias al dedupe por `(ticker, url)` en `news_articles`).

Verificado con un caso de prueba que reproduce el bug exacto (backfill completo con lotes de 10, luego cambio a lotes de 4): con la corrección, los 20 tickers de prueba siguen cubiertos al 100 % tras el cambio de tamaño de lote; sin ella, algunos se habrían perdido sin ningún error visible — el tipo de fallo silencioso más peligroso porque no sale en ningún log.

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

## Estado real del pipeline (primera ejecución completa, 2026-07-30)

Backbone ejecutado de punta a punta con datos reales sobre los 208 tickers de `config.TICKERS` (no sintéticos): `download.py` → `enrich_stocks.py` → `load.py` → `gold.py` → `model.py` → `predict.py`. Verificado por lectura directa de `data/stocker.db`:

- `daily_prices`: 208 tickers, 515.159 filas (varían por ticker según antigüedad real de cotización: p. ej. PLTR 1.462 filas, GEV 585, CEG 1.114 — no son errores, son OPVs recientes).
- `gold_train`: 208 tickers, 510.999 filas. `gold_inference`: 208 tickers, 208 filas (1 por ticker, la sesión más reciente).
- `predictions`: 208 filas (1 por ticker), `model_version=random_forest_20260730172436`.
- **Resultado del modelo en test (honesto, tal como exige este documento)**: Random Forest agrupado — acc=0.505, prec=0.513, rec=0.814. Baseline mayoritario — acc=0.516. Baseline persistencia — acc=0.496. **El modelo NO supera de forma consistente a ambos baselines.** Esto es un resultado válido a reportar, no un fallo del pipeline (ver "Contrato de evaluación" arriba) — con retornos diarios casi aleatorios, era el escenario esperado, no una sorpresa.

Pendiente de limpieza (no bloqueante):
- `stocks` tiene 5 filas huérfanas (FI, MMC, BK, DFS, HES) que ya no están en `config.TICKERS` tras la corrección de tickers — nunca se borraron de la tabla. Decidir si se eliminan o se dejan como registro histórico antes de construir el dashboard (si el dashboard lee `stocks` sin filtrar, aparecerían como tickers "fantasma").
- Las tablas legacy `gold_aapl_train`/`gold_aapl_inference` siguen existiendo (vacías, 0 filas) — falta ejecutar `db.drop_legacy_gold_tables()` para eliminarlas físicamente.
- `FISV` quedó con `sector`/`pais` en `NULL` tras `enrich_stocks.py` — yfinance no devolvió esos campos para este ticker en concreto (sí devolvió `nombre`). Requiere un parche puntual o una segunda pasada.
- Aviso recurrente de `load.py`: ~90 tickers reportan la misma sesión concreta (2026-07-24) como hueco de mercado inesperado. Al repetirse en tantos tickers a la vez (todos descargados el mismo día, 2026-07-28), probablemente sea un problema puntual de esa descarga por lote más que 90 fallos independientes — revisar si merece re-descarga antes de confiar en el histórico para producción.
- Sigue pendiente el módulo que rellena `predictions.actual_target_up_down` con el cierre real una vez conocido (ver README de `src/`).

## Dashboard (2026-08-05)

Primera versión construida tras confirmar el backbone con datos reales y automatizar el backfill de noticias en segundo plano. Decisión explícita: **empezar el dashboard ahora** (opción elegida por el usuario entre dashboard / backfill de aciertos reales / limpieza pendiente) — el backfill de noticias corre solo, así que no bloquea el resto del desarrollo.

**Framework: Streamlit** (decisión del desarrollador, no fue un conflicto con este documento que resolver) — velocidad de construcción dentro del mes disponible, soporte nativo multi-página y multi-usuario, y SQLite tolera bien lecturas concurrentes (el dashboard es de solo lectura, nunca escribe). Dash habría dado más control de layout a cambio de más tiempo, no justificado para el alcance actual. Detalle de páginas y diseño en `dashboard/README.md`.

**Selector de horizonte (día/semana/mes) — decisión explícita del usuario (2026-07-31)**: el mockup original pedía los tres horizontes, pero `gold.py`/`model.py` solo calculan el target a 1 sesión vista. Se dejó el selector construido en la UI tal como en el mockup, pero semana/mes aparecen deshabilitados con un aviso — honesto con lo que el modelo sabe hacer hoy, sin bloquear el dashboard esperando a entrenar más modelos. Si se decide extender a esos horizontes, hay que ampliar `gold.py` (targets a 5/20 sesiones vista) y `model.py` (un modelo por horizonte) antes de activar esas opciones.

**Cambio de diseño en `model.py`**: `save_model()` ahora persiste las métricas (modelo vs. ambos baselines, tamaños de split, fechas) dentro del propio `.joblib`, no solo el modelo — necesario para que el dashboard pueda mostrar "Rendimiento del modelo" sin reentrenar (~500k filas) en cada carga de página. Los modelos guardados antes de este cambio no tienen esas claves; la página lo detecta y pide reentrenar en vez de fallar o mostrar datos falsos.

**Honestidad de resultado también en el dashboard**: la página "Predicciones" superpone al histórico de cada ticker las predicciones que el modelo agrupado habría hecho (backtest), pero distingue visualmente (línea vertical + aviso) el periodo de test real (nunca visto en entrenamiento) del periodo de train — sin esa distinción, el % de aciertos mostrado por ticker sería engañoso (mezclaría datos que el modelo ya vio al entrenar con predicción genuina fuera de muestra). La métrica oficial, honesta y comparada contra los baselines, vive solo en "Rendimiento del modelo".

**`list_tickers()` (dashboard/data_access.py) lee de `daily_prices`, no de `stocks`** — evita a propósito que las 5 filas huérfanas de `stocks` (FI, MMC, BK, DFS, HES, ver arriba) aparezcan como tickers fantasma en el selector: como nunca tuvieron datos reales de precio, no tienen filas en `daily_prices`, así que quedan excluidas sin necesidad de limpiar `stocks` primero.

Probado con `streamlit.testing.v1.AppTest` (ejecuta cada página server-side sin necesitar navegador) contra una copia de la base de datos real de 208 tickers y un modelo reentrenado de prueba — las cuatro páginas cargan sin excepciones, incluyendo cambiar de ticker y seleccionar un horizonte deshabilitado. **Pendiente**: el modelo real del usuario (`models/random_forest_20260730172436.joblib`) se entrenó antes del cambio de `save_model()`, así que la página "Rendimiento del modelo" mostrará el aviso de "vuelve a entrenar" hasta que se ejecute `python src/model.py` de nuevo en su máquina.

## Rediseño visual del dashboard (2026-08-06)

El usuario pidió abandonar la estética por defecto de Streamlit para dar al proyecto una interfaz de "dashboard profesional". Antes de tocar código, se generaron mockups (HTML/SVG) con dos direcciones — A: sidebar oscura; B: navegación superior minimalista sin sidebar — y el usuario eligió B, con dos ajustes iterativos: separadores muy sutiles entre secciones, y más contraste de gris en las opciones no seleccionadas del selector de horizonte. Confirmado el diseño, se implementó tal cual en el dashboard real.

**Paleta**: blanco de base, azul `#1D4ED8` como acento principal, negro `#0B0F19` como texto. Verde/rojo se mantienen como única excepción, para acierto/falla y sube/baja — es una codificación semántica estándar (no decorativa) y cambiarla a formas hollow/filled se consideró innecesario dado el resto del rediseño.

**Se descartó salir de Streamlit** (Dash, etc.) por el mismo motivo de tiempo documentado en "Por qué Streamlit" — en su lugar se llegó al look buscado con: tema nativo (`.streamlit/config.toml`: `primaryColor`, `backgroundColor`, `textColor`, `client.toolbarMode = "minimal"` para ocultar el menú hamburguesa y el botón Deploy), CSS inyectado (`dashboard/ui.py`, dirigido a los `data-testid` reales del DOM de Streamlit 1.61 — `stHeader`, `stPageLink`, `stMetricLabel`, etc. — verificados leyendo el bundle JS instalado, no adivinados) y `st.navigation(position="top")` para la barra de navegación superior nativa en vez de la sidebar.

**Corrección real durante la implementación**: la primera versión puso las páginas en `dashboard/pages/0_Inicio.py`, etc. (igual que la app clásica de Streamlit). Eso rompió el arranque con `StreamlitAPIException: Multiple Pages specified with URL pathname Inicio` — Streamlit trata cualquier carpeta llamada `pages/` junto al entrypoint como app multipágina "clásica" (que también intenta registrar `Inicio.py` como página con ruta `Inicio`, la misma que `pages/0_Inicio.py`) incluso cuando el entrypoint ya usa `st.navigation()` explícitamente, y las dos vías chocan antes de que el propio código del entrypoint llegue a ejecutarse. Solución: renombrar la carpeta a `dashboard/views/` (sin significado especial para Streamlit) — ver el comentario en `views/inicio.py`. Verificado con `AppTest.switch_page("views/<archivo>.py")` contra las 4 páginas tras el cambio.

Detalle completo de la estructura de archivos en `dashboard/README.md`.

## Ampliación de features para intentar subir el accuracy (2026-08-06)

Punto de partida honesto (modelo real del usuario, entrenado el 2026-08-06 con las 6 features originales, `models/random_forest_20260806110812.joblib`): accuracy=0.505, no supera al baseline de clase mayoritaria (0.516). Con solo 6 features y sin tuning de hiperparámetros, había margen de mejora razonable antes de aceptar que el modelo simplemente no puede hacerlo mejor.

Se investigó qué añadir consultando material propio del usuario (curso de ML: resumen técnico de un proyecto anterior, diapositivas de Feature Engineering y de Optimización/Explicabilidad) cruzado con el estado real del código. El usuario eligió explícitamente **empezar por features** (frente a cambiar de algoritmo primero) entre varias opciones presentadas.

**Features nuevas** (`src/features.py`, `src/db.py`, `src/model.py`):
- `return_5d`, `return_20d`: momentum a más plazos que `return_1d` (misma naturaleza, solo cambia la ventana).
- `rsi_14`: RSI clásico (14 sesiones), con media móvil simple en vez de suavizado exponencial, para no mezclar dos estilos de suavizado distintos con `ma_5/10/20` sin necesidad. Ya acotado en [0,100], comparable entre tickers sin normalizar.
- `macd_line`/`macd_signal` (guardados) → `macd_hist_norm` (usado por el modelo): MACD estándar (12/26/9), con las EMAs correctamente enmascaradas durante el warm-up (33 sesiones) — `ewm()` no produce NaN por sí solo como `rolling()`, así que sin la máscara se habrían colado filas "completas" sin historial suficiente. Se guarda en unidades de precio (como `ma_5/10/20`) y se normaliza por `close` en `build_feature_matrix`, nunca en `features.py` — mismo criterio ya establecido para todas las features de precio.
- `price_std_20` (guardado) → `bb_pct_b`/`bb_width` (usados por el modelo): Bandas de Bollinger reconstruidas a partir de `ma_20` (ya existente, es el centro de la banda) y esta desviación típica — evita duplicar la misma media dos veces en el esquema.
- `volume_ma_10` (guardado) → `relative_volume` (usado por el modelo): volumen de hoy frente a su propia media de 10 sesiones. Corrige (no elimina del todo — se mantiene `log_volume` también) la limitación ya documentada de que el volumen en bruto no es comparable entre una acción muy líquida y una poco líquida.
- `day_of_week`: 0 (lunes) a 4 (viernes), sin one-hot por ahora — válido para Random Forest (modelo por defecto), pero si en el futuro `logistic` pasa a ser el modelo principal habría que pasarlo a one-hot (una regresión lineal sí asumiría un orden numérico falso entre días).

**Casos borde manejados explícitamente** (verificado con un ticker sintético de precio perfectamente plano): si `price_std_20` o `volume_ma_10` salen en 0 (precio/volumen sin ninguna variación en la ventana — rarísimo con datos reales), `bb_pct_b`/`bb_width`/`relative_volume` se fijan en su límite natural (0.5/0/0) en vez de propagar NaN o dividir por cero dentro de sklearn. Si el precio está perfectamente plano más tiempo (RSI con avg_gain=avg_loss=0), `rsi_14` sale en NaN por diseño y esa fila se descarta igual que cualquier otra fila con historial insuficiente — no se fabrica un valor.

**Sentimiento de noticias — deliberadamente NO incluido todavía**: al revisar la base de datos real antes de implementar esto, `news_articles` seguía en **0 filas** pese a llevar automatizado desde el 31/07 — el log (`data/news_cron.log`) muestra que la ejecución diaria agota el cupo de Alpha Vantage tras UNA sola llamada en vez de las 25 esperadas, y en una ejecución el contador de ventanas pendientes incluso subió (500→504) en vez de bajar. Esto apunta a un problema real (cuota compartida con otra causa, o un cambio de configuración entre ejecuciones) que queda pendiente de diagnosticar aparte — no bloquea esta ampliación de features, pero significa que añadir `news_sentiment_daily` como feature ahora mismo solo metería una columna constante sin señal real. Pendiente explícito para cuando haya cobertura de verdad.

**Migración de esquema**: `gold_train`/`gold_inference` cambiaron de columnas (nuevas features crudas). Como son 100% derivadas de `daily_prices` (se recalculan por completo en cada `gold.py`, nunca hay dato "a mano" que perder), la migración en `db.py` simplemente las elimina y las deja que `gold.py` las repueble, en vez de un ALTER TABLE incremental — mismo criterio ya usado para `news_backfill_progress`. También se subió `MIN_HISTORY_ROWS_FOR_GOLD` de 30 a 40: con MACD necesitando 33 sesiones de warm-up, el mínimo antiguo podía dejar a algunos tickers con 0 filas útiles sin avisar con claridad.

**Robustez añadida de paso**: `model.read_gold_train`, `gold.read_daily_prices` y `dashboard/data_access.get_gold_train_for_ticker` usaban listas de nombres de columna escritas a mano que tenían que coincidir EXACTAMENTE (mismo orden) con el esquema de `db.py` en tres archivos distintos — con cada ampliación de features esto es un punto de fallo silencioso real (una lista desincronizada no rompe en tiempo de importación, asigna nombres de columna incorrectos sin avisar). Se cambiaron las tres a `result.keys()` (nombres reales devueltos por SQLAlchemy), eliminando la necesidad de mantener esas listas sincronizadas a mano en el futuro.

**Pendiente para el usuario**: ejecutar `python src/gold.py` (dispara la migración de esquema y repuebla `gold_train`/`gold_inference` con las features nuevas para los 208 tickers) y después `python src/model.py` (reentrena con las 14 features) en su máquina real — no se ha reentrenado sobre los datos reales de 208 tickers desde este sandbox de desarrollo (~500k filas más `n_estimators=300` supera el límite de tiempo de esta herramienta). Verificado en su lugar con un pipeline sintético de extremo a extremo (`db.py` → `gold.py` → `model.py` → `predict.py`, incluyendo el caso borde de precio plano) que confirma que no hay excepciones, ni NaN/inf en la matriz de features, y que el bundle guardado tiene las 14 features nuevas.
