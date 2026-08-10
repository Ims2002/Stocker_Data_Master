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

**Rediseño de noticias: 1 ticker por llamada, backfill a 6 meses (2026-08-06)**

Tras varios días de ejecuciones reales con `NEWS_TICKERS_PER_CALL=10`, el usuario compartió un log real (`news_cron.log`) para decidir si recortar el backfill de 24 a 6 meses. Revisar el log destapó dos problemas reales, uno de ellos mucho más grave que el motivo original de la consulta:

1. **`news_articles` llevaba en 0 filas desde el principio**: se inspeccionaron los 27 ficheros JSON crudos guardados hasta la fecha en `data/raw/news/` (distintos lotes de 10 tickers, distintas ventanas de fechas desde 2024-08) y los 27 tenían `"feed": []` — cero artículos, sin excepción. Es estadísticamente imposible que no haya habido NINGUNA noticia de NVDA/AAPL/MSFT/AMZN/GOOGL/TSLA/META/... (los lotes eran de mega-caps) en meses enteros si el parámetro `tickers=A,B,C,...` de `NEWS_SENTIMENT` funcionara con semántica OR ("cualquiera de estos tickers"), que es la asunción sobre la que se diseñó todo el sistema de lotes desde el principio (ver más arriba, "agrupar varios tickers por llamada" listado como ventaja de Alpha Vantage). La hipótesis mejor respaldada por esta evidencia es que el parámetro usa semántica **AND** (el artículo tiene que mencionar TODOS los tickers del lote a la vez) — con lotes de 10 tickers no relacionados, esto hace casi imposible que devuelva nada nunca. **No confirmado en documentación oficial** (la página de Alpha Vantage se carga por JS, no accesible ni vía fetch plano ni vía Claude in Chrome — extensión no disponible en este entorno — y no se ha gastado cupo real en probarlo directamente porque es un recurso demasiado escaso para tantear a ciegas), pero la evidencia circunstancial (0/27) es contundente. **Conclusión práctica**: recortar el backfill a 6 meses (como proponía el usuario) no habría arreglado el problema de fondo — con lotes de 10 tickers seguiría devolviendo 0 artículos, solo que en menos tiempo.
2. **`TICKER_SYMBOL_OVERRIDES = {"BRK-B": "BRK.B"}` estaba activamente mal**: el mismo log trae el error textual de la API — `"Invalid ticker format: BRK.B. Ticker can only contain alphanumeric characters, colons, underscores, and hyphens"` — es decir, el punto es justo el carácter que Alpha Vantage NO permite; el guion sí está en su lista de caracteres válidos. La sección de arriba (2026-07-30) ya había descartado que el formato de `BRK-B` fuera la causa PRINCIPAL de los fallos masivos, pero seguía dando por hecho que el mapeo en sí era correcto/inofensivo — no lo era, era un bug real e independiente que garantizaba que el lote con `BRK-B` fallara siempre.

**Fix aplicado** (`src/config.py`, `src/news.py`):
- `NEWS_TICKERS_PER_CALL`: 10 → **1**. Único tamaño de lote que garantiza intersección no vacía si la semántica es AND. `ticker_batches()` no necesitó cambios de código, solo el valor de config.
- `TICKER_SYMBOL_OVERRIDES`: se vacía (de `{"BRK-B": "BRK.B"}` a `{}`) — `BRK-B` se envía tal cual, sin traducir. El mecanismo se deja listo (dict vacío, no eliminado) por si algún ticker futuro sí lo necesita.
- `NEWS_BACKFILL_MONTHS`: 24 → **6** (decisión explícita del usuario, confirmada independientemente del hallazgo anterior — 6 meses le parece suficiente para el propósito del proyecto).
- `NEWS_BACKFILL_WINDOW_DAYS`: 30 → **180**, para que los 6 meses de backfill sean exactamente 1 ventana por ticker. Con `NEWS_TICKERS_PER_CALL=1`, el backfill completo son **208 llamadas totales** (1 por ticker) ≈ **~9 días** a 25 llamadas/día — más rápido que la estimación anterior de ~21 días, y esta vez con datos reales en cada llamada en vez de 0 artículos garantizados. Riesgo aceptado: el `limit=1000` de la API por llamada podría truncar tickers muy mediáticos (TSLA, NVDA) si superan 1000 artículos en 6 meses; con `sort=RELEVANCE` se quedarían los más relevantes según el propio scoring de Alpha Vantage, no un recorte cronológico arbitrario.
- **`run_daily_update()` tenía un bug latente que este cambio habría disparado**: con lotes de 10 (~21 lotes), un `python news.py --daily` hacía ~21 llamadas, por debajo del presupuesto diario de 25. Con lotes de 1 (208 lotes), la misma función sin cambios habría intentado 208 llamadas en una sola ejecución — 8 veces el cupo diario. Corregido: `run_daily_update()` ahora tope a `NEWS_DAILY_CALL_BUDGET` llamadas por ejecución y elige una **muestra aleatoria** de tickers cada vez (en vez de round-robin con estado persistido, más simple de mantener) — en expectativa, cada ticker se revisa cada ~208/25 ≈ 8-9 días, no a diario estricto. Trade-off aceptado (frescura de noticias más laxa) para no meter una tabla de cursor/estado nueva solo para esto; si en el futuro hace falta un ciclo estrictamente round-robin, es el punto a revisar.
- **`news_backfill_progress` limpiada** (23 filas borradas, todas de lotes de 10 tickers): no hacía falta estrictamente — `_batch_key()` es un hash de la composición exacta del lote, así que un lote de 1 ticker nunca coincide con la clave de un lote de 10, esas filas habrían quedado huérfanas e inofensivas de todas formas — pero se limpiaron por higiene, ya que corresponden a ejecuciones que (por el punto 1) no guardaron ningún artículo real. `news_articles` seguía en 0 filas, así que no hay ningún dato real que perder.

**Honestidad de resultado**: la hipótesis AND-vs-OR no está confirmada por documentación oficial, solo por evidencia circunstancial fuerte (0/27). Si resulta ser incorrecta y el problema fuera otra cosa, el fix a 1-ticker-por-llamada sigue siendo seguro (nunca puede ir a peor que el estado anterior, que ya era 0 artículos) pero sería más lento de lo necesario.

**Confirmado empíricamente (2026-08-07)**: primera ejecución real bajo el diseño nuevo (1 ticker/llamada) — el usuario reporta que ya llegan artículos reales (`news_articles` > 0 filas), no el "0 filas guardadas" constante de antes. Da soporte adicional a la hipótesis AND (aunque sigue sin confirmación oficial de Alpha Vantage, ahora hay evidencia funcional, no solo circunstancial). Backfill en curso, ~9 días a 25 llamadas/día.

**Bug real de no-convergencia del backfill, encontrado y corregido (2026-08-08)**. Al revisar un log con dos ejecuciones reales seguidas, algo no cuadraba: ambas mostraban "208/208 ventanas pendientes" al arrancar (no 183, que es lo que debería quedar tras la primera) y cada una procesaba los mismos lotes 0-24, pero con la ventana de fechas desplazada un día entre una ejecución y la siguiente (`2026-02-08→2026-08-07` en la primera, `2026-02-09→2026-08-08` en la segunda). Causa raíz: `date_windows()` calculaba `end = dt.date.today()` por defecto — como "hoy" avanza un día en cada ejecución del cron, **todas** las ventanas (no solo la más reciente) se recalculaban con fechas de inicio distintas cada día. La tabla `news_backfill_progress` usa `(batch_key, window_start)` como clave de "ya hecho": si `window_start` nunca se repite entre ejecuciones, nada queda nunca marcado como hecho de forma persistente, y el backfill vuelve a "todo pendiente" cada vez que corre, sin converger jamás, sin importar cuántos días se le dejara funcionando.

Este bug **no lo introdujo el rediseño de hoy** — estaba presente desde el diseño original (24 meses / ventanas de 30 días): con `end` ligado a "hoy", el `start` de TODAS las ventanas se desplaza igual cada día. De hecho explica una anomalía ya vista y mal diagnosticada en su momento (log del 2026-08-06, sección "Rediseño de noticias" más arriba): el contador de ventanas pendientes subió de 500 a 504 entre dos ejecuciones en vez de bajar — no era confusión de cupo, era este bug recalculando una lista de ventanas ligeramente distinta cada vez.

**Fix**: `date_windows()` para el backfill ya no usa `dt.date.today()` — se ancla a `config.NEWS_BACKFILL_REFERENCE_DATE` (fecha fija, `2026-08-08`, no se actualiza sola). Con esto, `window_start` es estable entre ejecuciones y el progreso se acumula de verdad. `run_daily_update()` no se ve afectado — no usa `date_windows()`, calcula "ayer→hoy" directamente en cada llamada, donde sí es correcto que dependa del día real.

**Progreso recuperado, no descartado**: `news_backfill_progress` se limpió (las 50 filas de esta semana quedaban ancladas a ventanas que ya no se iban a repetir con el fix), pero antes se verificó que la segunda ejecución del log (ventana `2026-02-09→2026-08-08`, lotes 0-24, sin ningún fallo) coincide EXACTAMENTE con la ventana fija nueva — así que esos 25 tickers se marcaron como completados a mano en vez de dejar que se re-consultaran innecesariamente (los artículos ya están guardados en `news_articles`, 24.486 filas reales verificadas — no se tocaron). Verificado tras el fix: `pending_work_items()` reconoce esos 25 como hechos y continúa justo en el ticker 26 (`CAT`), con 183 pendientes — comportamiento correcto, confirmado con una simulación de "ejecución futura" que reproduce la misma lista de pendientes en vez de resetear a 208.

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
- ~~`stocks` tiene 5 filas huérfanas (FI, MMC, BK, DFS, HES)~~ — eliminadas (2026-08-07): verificado antes de borrar que las 5 tenían 0 filas en `daily_prices`/`gold_train`/`predictions`, así que no había ninguna FK que respetar ni dato real que perder. `stocks` queda con exactamente 208 filas, igual que `config.TICKERS`.
- ~~Las tablas legacy `gold_aapl_train`/`gold_aapl_inference`~~ — eliminadas (2026-08-07) vía `db.drop_legacy_gold_tables()` (ya existía, solo faltaba ejecutarla contra la base real).
- ~~`FISV` con `sector`/`pais` en `NULL`~~ — **aceptado como limitación conocida de la fuente (2026-08-07)**: el usuario reejecutó `python src/enrich_stocks.py FISV` en su máquina real y yfinance sigue devolviendo `sector=None, pais=None` para este ticker en concreto (sí trae `nombre`) — no era un fallo transitorio de red, es un hueco real en los metadatos que expone yfinance para FISV. Decisión explícita: no perseguirlo más (`sector`/`pais` son solo informativos/de filtro, no se usan como feature del modelo — ver "Enfoque de modelado"), y no fijarlo a mano en `config.py` para no añadir mantenimiento por un campo sin impacto real.
- **Hueco de mercado real en 2026-07-24** (confirmado, sigue pendiente — misma limitación de red): no son ~90 tickers, son **112** (verificado por consulta directa a `daily_prices`: 208 tickers tienen fila en 2026-07-23, solo 96 en 2026-07-24). El 24/07/2026 es viernes, sesión normal de NYSE, así que es un hueco real de datos, no un festivo — lo más probable es un fallo puntual de `yfinance`/Yahoo ese día concreto para la mitad del universo, no 112 fallos independientes. Fix: ejecutar `python src/download.py` (sin `--daily`, backfill completo — trae de nuevo los 10 años, incluyendo el 24/07) seguido de `python src/load.py`, que hace upsert por `(ticker, date)` y rellena el hueco sin duplicar el resto del histórico ya cargado.
- ~~Sigue pendiente el módulo que rellena `predictions.actual_target_up_down` con el cierre real una vez conocido~~ — implementado, ver "Seguimiento real de predicciones (2026-08-07)" más abajo.

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

**Bug real encontrado y corregido de paso**: `predict.py` tenía su propia lista de columnas de `gold_inference` escrita a mano, y se quedó desincronizada con el esquema nuevo (13 columnas viejas vs. 19+ columnas reales) — habría asignado mal los nombres de columna en la próxima ejecución de `predict.py`, sin ningún error visible (bug silencioso). Corregido con el mismo criterio que el resto (`result.keys()`).

**Resultado real, con el modelo que el usuario reentrenó el mismo día (2026-08-06, `random_forest_20260806113832.joblib`, 14 features)**: accuracy=0.505, prácticamente igual que con las 6 features originales (0.505) — sigue sin batir al baseline mayoritario (0.516). Las features nuevas cambiaron el comportamiento del modelo (el recall bajó de 81% a 76%) pero no el resultado agregado. Importancia de features: nada domina (todo entre 4-14%); `return_1d`/`return_5d` siguen pesando más que los indicadores nuevos.

## Plan B: Gradient Boosting + tuning (2026-08-06)

Con la ampliación de features sin mover la aguja, el usuario pidió probar la segunda opción ya planteada de antemano: cambiar de algoritmo (Random Forest → Gradient Boosting) con tuning de hiperparámetros — **dejando el modelo activo del dashboard tal cual está**, esto es explícitamente un experimento, no un reemplazo todavía.

**Implementación (`src/model.py`, reutilizable, no un script suelto)**:
- `_build_model()` gana `model_type="lightgbm"` (import perezoso — no es una dependencia obligatoria del resto del pipeline) con hiperparámetros por defecto razonables, sin tuning.
- `tune_lightgbm(df, n_iter, n_splits)`: búsqueda aleatoria de hiperparámetros (rangos estándar de `num_leaves`, `max_depth`, `learning_rate`, `n_estimators`, `min_child_samples`, `feature_fraction`, `bagging_fraction`) evaluada con **`_time_series_folds()`** — cortes cronológicos de ventana creciente (walk-forward), NUNCA k-fold aleatorio como `StratifiedKFold` (la técnica que aparece en los apuntes de referencia del usuario para tuning en general): con datos de series temporales, un k-fold aleatorio mezclaría fechas futuras dentro del "entrenamiento" de cada fold, la misma fuga de información que el "Contrato de evaluación" de este proyecto prohíbe explícitamente para el split train/test. El test real oficial (`temporal_split`) no se toca en ningún momento durante la búsqueda — solo al final, una vez fijados los hiperparámetros ganadores.
- CLI: `python model.py --model lightgbm --tune [--tune-iterations N]`.
- `save_model()` guarda `best_params`/`cv_best_val_accuracy` cuando el resultado viene de tuning (no guarda `tuning_trials` completo, es solo de interés puntual).

**Ejecución real** (sobre los 508.087 filas reales de `gold_train`, no sintéticas): 20 combinaciones de hiperparámetros x 3 folds walk-forward = 60 entrenamientos de validación + 1 reentrenamiento final sobre el train oficial completo (482.609 filas) y evaluación sobre el test real (25.478 filas, nunca visto durante la búsqueda). Nota técnica: se hizo por lotes pequeños desde este sandbox de desarrollo (el fit de LightGBM es rápido, ~3-4s sobre 480k filas, pero el límite de 45s por comando de esta herramienta obligó a trocear la búsqueda en varias llamadas, con los resultados de cada trial persistidos en disco entre llamadas) — irrelevante para el resultado en sí, solo una limitación operativa de cómo se ejecutó.

**Resultado (test real)**:

| Método | Accuracy | Precision | Recall |
|---|---|---|---|
| LightGBM tuneado | 0.507 | 0.516 | 0.715 |
| Random Forest (mismo día, 14 features) | 0.505 | 0.514 | 0.760 |
| Baseline mayoritario | 0.516 | 0.516 | 1.000 |
| Baseline persistencia | 0.496 | 0.512 | 0.513 |

LightGBM tuneado mejora ligeramente sobre Random Forest (+0.3 puntos), pero **sigue sin batir al baseline mayoritario**. Los 20 valores de accuracy media en validación de la búsqueda de hiperparámetros están todos apretados entre 0.504 y 0.508 — no hay ninguna combinación que destaque, lo que descarta que el problema sea "hiperparámetros mal elegidos" en vez de "el techo de señal real en estas features es este".

**Conclusión honesta (ver "Honestidad de resultado")**: ni ampliar de 6 a 14 features ni cambiar de Random Forest a Gradient Boosting con tuning ha movido el accuracy fuera del rango ~50-51%, todos por debajo o igual al baseline más simple posible. Es un resultado consistente con la hipótesis de mercados eficientes a horizonte de 1 día — con precio e indicadores técnicos derivados del precio, no parece haber señal explotable en este universo de 208 acciones a este plazo. Las palancas que quedan sin probar y que SÍ podrían cambiar esta conclusión: sentimiento de noticias real (bloqueado por la cuota de Alpha Vantage, ver arriba), un horizonte de predicción distinto a 1 día (semana/mes, requeriría ampliar `gold.py`), o aceptar este resultado y documentarlo como una contribución igual de válida para el TFM (un "no hay señal explotable con este enfoque" bien fundamentado, con la metodología correcta, es un resultado legítimo).

**Decisión final: se mantiene Random Forest como modelo de producción/dashboard (2026-08-06)**. La diferencia de accuracy entre LightGBM tuneado (0.507) y Random Forest (0.505) se verificó con un **test de McNemar** (test pareado sobre las discrepancias de predicción entre ambos clasificadores en el mismo conjunto de test) — resultado **no significativo** (p ≈ 0.17, muy por encima de cualquier umbral habitual de 0.05), es decir, la diferencia observada es consistente con ruido, no con una mejora real de LightGBM sobre Random Forest. Dado que Random Forest ya es el modelo activo, más simple y sin hiperparámetros que mantener/re-tunear, el usuario decidió no reemplazarlo. Random Forest sigue siendo el modelo de producción del dashboard; el experimento de LightGBM queda documentado aquí pero no se ha guardado ningún artefacto (`.joblib`) de esa rama en `models/` — solo los resultados de este informe.

## Seguimiento real de predicciones (2026-08-07)

Con el modelo ya cerrado (Random Forest) y las noticias arregladas y en marcha, el usuario pidió seguir con el frente pendiente más antiguo de "Estado real del pipeline": el módulo que resuelve `predictions.actual_target_up_down` con el cierre real, necesario para medir rendimiento real en producción vs. backtest (parte obligatoria del "Contrato de evaluación", no una mejora opcional).

**Implementación (`src/track_predictions.py`, nuevo)**:
- Lee las filas de `predictions` con `actual_target_up_down IS NULL`.
- Para cada una, busca en `daily_prices` el cierre de `date_predicha` y el de la fila inmediatamente anterior en el histórico de ESE ticker (mismo criterio que `gold.py`: `close_next_day = close.shift(-1)` sobre la serie ordenada por fecha, no "el día natural de antes" — así el resultado coincide exactamente con lo que `gold.py` habría calculado si esa fecha ya fuera parte del histórico de entrenamiento).
- Si el cierre de `date_predicha` todavía no está en `daily_prices`, la predicción se deja tal cual (se reintenta sola la próxima ejecución) — no hay estado especial que gestionar, la propia condición `IS NULL` hace de cola de pendientes.
- Empates (`close(date_predicha) == close(sesión anterior)`) cuentan como `actual_target_up_down=0` ("baja/igual"), igual que en `gold.py`.
- Verificado con datos sintéticos: caso sube (1), caso empate (0), caso pendiente (sin cierre real todavía, se omite), y caso borde de un ticker sin ninguna sesión anterior registrada (se omite, no se puede calcular). Ejecutado también contra la base de datos real: de las 208 predicciones existentes (todas de la primera ejecución del pipeline, 2026-07-30, `date_predicha` 2026-07-29/31), **0 se resolvieron** — motivo correcto, no un bug: `daily_prices` no se ha vuelto a actualizar desde entonces (p. ej. AAPL solo llega hasta 2026-07-28), así que ninguna tiene su cierre real disponible todavía.

**Hallazgo de paso: solo `news.py` está automatizado, no el resto del pipeline**. Al revisar por qué `daily_prices` estaba desactualizado se confirmó que el Task Scheduler del usuario solo tiene programado `run_news_daily.bat` — `download.py`/`load.py`/`gold.py`/`predict.py` (y ahora `track_predictions.py`) nunca se ejecutan solos, dependen de que el usuario los lance a mano. Sin esto, `track_predictions.py` nunca tendría datos nuevos que resolver por mucho que se ejecute. Se añadió `run_daily_pipeline.bat` (mismo patrón que `run_news_daily.bat`: `PYTHONUTF8=1`, log en `data\pipeline_cron.log`) que encadena `download.py → load.py → gold.py → predict.py → track_predictions.py` una vez al día. Pendiente que el usuario lo programe con el Task Scheduler (ver `src/README.md`) — no se ha tocado su Task Scheduler desde aquí, solo se ha creado el `.bat`.

**Corrección tras revisión del usuario: `download.py` no debe pedir el histórico completo cada día (2026-08-07)**. Al montar `run_daily_pipeline.bat`, el usuario señaló que llamar a `download.py` sin más para una tarea DIARIA pediría `HISTORY_PERIOD` (10 años) completo a `yfinance` para los 210 tickers cada vez — mucho más pesado de lo necesario para traer, en la práctica, 1 sesión nueva, y además `save_raw_csv` escribiría un CSV nuevo de ~2.500 filas por ticker cada día en `data/raw/` para siempre (crecimiento de disco innecesario). Corregido: `download.py` gana el flag `--daily`, que pide solo `config.DOWNLOAD_DAILY_LOOKBACK_DAYS` días (10 por defecto, con margen para cubrir fines de semana/festivos de mercado/algún día en que la tarea programada no llegara a ejecutarse) en vez de `HISTORY_PERIOD`. El solape con lo ya cargado es intencional y seguro: `load.py` hace upsert por `(ticker, date)`, así que repetir unas pocas sesiones ya conocidas no duplica nada. `run_daily_pipeline.bat` ya usa `download.py --daily`; el modo sin flags (backfill completo) se mantiene intacto para la primera carga de un ticker nuevo. Verificado con `yf.Ticker` sustituido por un doble de prueba (sin gastar llamadas reales a la API): `--daily` pide `start=hoy-10`, sin flags sigue pidiendo `period=HISTORY_PERIOD` como antes.
