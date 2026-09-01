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

## Preparación de noticias como feature (2026-08-08)

Con el backfill de noticias ya avanzando de verdad (24.486 artículos reales, 25/208 tickers cubiertos y subiendo ~25/día), el usuario pidió dejar preparada la integración de `news_sentiment_daily` como feature del modelo — sabiendo que con solo 25/208 tickers cubiertos todavía no toca reentrenar con esto en serio (ver aviso más abajo), pero para no tener que desarrollarlo de cero cuando el backfill termine (~8 días más).

**Features nuevas** (`features.py`, `db.py`, `gold.py`, `model.py`):
- `news_sentiment_3d`: media de `avg_sentiment_score` (de la vista `news_sentiment_daily`) suavizada a 3 sesiones. Ventana corta a propósito — a diferencia de `ma_20`/`rsi_14`, la relevancia de una noticia decae rápido, y la profundidad histórica real tampoco da para más (`NEWS_BACKFILL_MONTHS=6`).
- `news_volume_3d` (guardado) → `news_volume_log` (usado por el modelo, `log1p`): nº de artículos sumado en las últimas 3 sesiones — mismo criterio que `log_volume`/`relative_volume`: sin transformar no sería comparable entre un ticker muy mediático y uno que casi no sale en prensa.
- `news_sentiment_3d` SÍ se usa tal cual (ya acotado en un rango tipo [-1,1], comparable entre tickers, igual que `rsi_14`).

**"Sin noticias" es un valor real (0), no NaN** — mismo criterio ya establecido para `bb_pct_b`/`relative_volume`: la ausencia de cobertura es informativa ("nadie ha escrito sobre esta acción estos días"), no un dato ausente que haya que enmascarar. `features.attach_news_features()` hace un LEFT JOIN de la vista `news_sentiment_daily` (que solo tiene fila para los días con al menos un artículo) contra el calendario completo del ticker, rellena con 0 los días sin cobertura, y SOLO DESPUÉS aplica el `.rolling(3)` — así el `.rolling()` nunca ve un NaN salvo en las primeras 2 filas de calentamiento de cada ticker (mismo criterio de "nunca ventana parcial" que el resto de indicadores). Funciona igual de bien si el ticker no tiene ninguna fila en `news_articles` todavía (la inmensa mayoría, mientras el backfill sigue en marcha): sale 0 en las dos columnas para toda su historia, no un error.

**Migración de esquema**: `gold_train`/`gold_inference` ganan `news_sentiment_3d`/`news_volume_3d` (mismo patrón de migración ya usado dos veces — drop + recrear vacía, se repueblan solas con `python gold.py`, son 100% derivadas). El marcador de `_migrate_gold_tables()` pasó de `rsi_14` a `news_sentiment_3d` (la columna más reciente), cubre cualquier esquema anterior sin comprobar cada columna añadida una por una.

**Verificado con datos sintéticos** (no con datos reales — ver aviso abajo sobre por qué no toca reentrenar todavía): pipeline completo `db.py → gold.py → model.build_feature_matrix` con 3 tickers, uno con noticias sintéticas dispersas y dos sin ninguna. Confirmado: sin NaN/inf en la matriz de features final, el ticker sin cobertura sale con las dos columnas en 0 en toda su historia, la señal del ticker con noticias se concentra correctamente en la ventana de 3 sesiones alrededor de cada artículo y decae después. También verificado el camino de `predict.py` (features sobre `gold_inference`, una sola fila).

**AVISO — no reentrenar todavía solo por esto**: con el backfill cubriendo solo 25/208 tickers a día de hoy, la inmensa mayoría de filas de `gold_train` tendrían estas dos columnas en 0 sin más — reentrenar ahora no mediría la señal real de la feature, solo añadiría dimensionalidad sin información, y podría incluso enmascarar sutilmente la importancia de las demás features en el reporte del dashboard. Esperar a que el backfill avance bastante más (o termine del todo, ~8 días a este ritmo) antes de ejecutar `python gold.py` + `python model.py` con la intención de evaluar esta feature en serio.

**Nota (2026-08-13)**: el usuario reentrenó de todas formas el 2026-08-10 (`random_forest_20260810181458`, 16 features, backtest accuracy 0.5047 — prácticamente igual a los modelos anteriores, 0.505/0.505, tal como se esperaba con cobertura de noticias aún parcial). Se documenta aquí para que quede constancia de qué modelo está activo y cuándo se entrenó, sin cambiar la recomendación de arriba: sigue sin ser una evaluación real de la feature de noticias hasta que el backfill tenga más cobertura.

## Seguimiento real de predicciones: primer análisis (2026-08-13)

Con `track_predictions.py` ya funcionando en producción (tarea diaria programada desde hace unos días), se acumularon 1.037 predicciones reales resueltas (de 1.246 totales). El acierto agregado sale en **44,4%** — notablemente por debajo del ~50-51% del backtest. Investigado antes de sacar ninguna conclusión (ver "Honestidad de resultado"):

**Causa principal: el agregado mezcla 3 `model_version` distintos en condiciones de mercado muy distintas, no es una comparación limpia.**

| Fecha predicha | model_version | nº predicciones | % predijo "sube" | % subió de verdad | Acierto |
|---|---|---|---|---|---|
| 2026-07-29 | `random_forest_20260730172436` (el primero, 6 features) | 204 | 68,1% | 31,9% | **29,4%** |
| 2026-07-31 | mismo | 3 | — | — | 33,3% (muestra ínfima) |
| 2026-08-07 | `random_forest_20260806113832` (14 features) | 208 | 75,5% | 61,1% | 51,0% |
| 2026-08-10 | mismo | 208 | 71,6% | 45,2% | 44,7% |
| 2026-08-11 | mismo | 207 | 33,8% | 57,5% | 44,4% |
| 2026-08-12 | `random_forest_20260810181458` (16 features) | 207 | 43,0% | 37,7% | 52,2% |

El día que más pesa hacia abajo es 2026-07-29: 204 predicciones (el 20% del total) con el modelo MÁS ANTIGUO y solo 6 features, en una sesión donde el mercado real fue mayoritariamente bajista (68% de tickers subieron según predijo el modelo, pero solo el 31,9% subió de verdad) — parece una sesión real de caída generalizada que ese modelo (entrenado semanas antes, sin ver ese régimen) no anticipó, no un bug. Quitando ese modelo del cálculo, el acierto agregado sube a **48,1%** (830 predicciones) — mucho más cerca del backtest, aunque todavía ligeramente por debajo.

**No hay un sesgo direccional consistente día a día**: unos días el modelo sobre-predice "sube" (07-29, 08-07, 08-10), pero el 08-11 predijo mayoritariamente "baja" y el mercado subió — es el patrón contrario. Esto es más compatible con ruido normal de mercado que con un sesgo sistemático del modelo.

**Limitación estadística importante a tener en cuenta**: cada "día" son ~208 predicciones, pero NO son 208 observaciones independientes — son 208 tickers reaccionando (con bastante correlación entre ellos) al mismo único evento de mercado de esa sesión. En la práctica hay **5-6 sesiones reales independientes** de datos, no 1.037. Con una muestra así de pequeña, un accuracy del 44% o del 52% en un día concreto no dice casi nada por sí solo — hace falta acumular bastantes más semanas de seguimiento real antes de poder concluir si hay degradación real vs. backtest, tal como ya advierte el "Contrato de evaluación" sobre no sacar conclusiones prematuras.

**Conclusión por ahora**: no hay evidencia de un bug ni de una degradación clara del modelo — el número bajo inicial es sobre todo un artefacto de mezclar un modelo obsoleto con un día de mercado atípico. Se sigue vigilando; sin acción inmediata.

**Página nueva en el dashboard: "Día a día" (`dashboard/views/seguimiento_real.py`, 2026-08-13)** — cierra el hueco identificado arriba. Muestra el acierto real de `predictions` (nunca backtest) SIEMPRE separado por `model_version` — tabla resumen (una fila por versión: rango de fechas, nº de sesiones de mercado, nº de predicciones, acierto real) y un gráfico de evolución día a día de la versión elegida en un selector, con el aviso explícito de que ~208 predicciones de un mismo día son 1 sola observación de mercado correlacionada, no 208 independientes (la lección de esta misma sección). Probado con `AppTest` contra la base real: sin excepciones, incluyendo cambiar de versión de modelo en el selector.

## ¿Ayuda el sentimiento de noticias a acertar más? Investigación (2026-08-13)

Con el backfill ya cubriendo 100/208 tickers (96.192 artículos reales, feb-ago 2026), el usuario pidió comprobar de verdad si el sentimiento aporta señal — no ya "preparar el código" (eso ya estaba hecho, ver "Preparación de noticias como feature"), sino mirar los datos reales. Se hizo como investigación aparte, sin tocar el modelo de producción ni `models/`.

**Método** (por limitación de cómputo de este sandbox — 2 CPUs, no las del PC real del usuario — se restringió el experimento a un subconjunto en vez de reentrenar los ~510k filas completos con 300 árboles como hace `model.py` en producción; ver nota de honestidad al final):
- Subconjunto: filas de los 100 tickers con cobertura real, desde el inicio de la ventana de backfill (2026-02) — 13.300 filas, de las cuales 9.753 con cobertura real (`news_volume_3d > 0`).
- Correlación punto-biserial entre `news_sentiment_3d` (y `news_volume_3d`) y `target_up_down`.
- Comparación adicional con el sentimiento SIN suavizar (solo el día), ponderado por `relevance_score`, y usando solo artículos de alta relevancia (>0.6) — para descartar que el problema fuera la propia forma de calcular la feature, no la señal en sí.
- Random Forest con los mismos hiperparámetros que `model.py` (300 árboles, profundidad 8), entrenado dos veces sobre el mismo subconjunto y split temporal: una vez con las 16 features (incluye noticias), otra con las 14 originales — comparando accuracy y mirando la importancia de features del modelo con noticias.

**Resultado — sin señal real, de forma consistente en todas las variantes probadas**:

| Comprobación | Resultado |
|---|---|
| Correlación `news_sentiment_3d` (suavizado 3 sesiones) vs. target | r = -0.007, p = 0.47 (no significativo) |
| Correlación `news_volume_3d` vs. target | r = 0.018, p = 0.08 (no significativo) |
| Correlación sentimiento sin suavizar (solo el día) vs. target | r = -0.008, p = 0.42 |
| Correlación sentimiento ponderado por relevancia vs. target | r = -0.009, p = 0.41 |
| Correlación solo artículos de alta relevancia (>0.6) vs. target | r = -0.011, p = 0.30 |
| Accuracy Random Forest CON noticias (subset, test 2 meses) | 49,26% |
| Accuracy Random Forest SIN noticias (mismo subset/split) | 49,33% (prácticamente igual) |
| Baseline mayoritario (mismo subset/split) | 52,4% (ninguno de los dos modelos lo bate) |
| Importancia de `news_sentiment_3d`/`news_volume_log` en el modelo con noticias | Las DOS features menos importantes de las 16, muy por debajo de cualquier indicador técnico |

Tasa de subida real casi idéntica según el sentimiento: 50,5% en días de sentimiento claramente positivo (>0.15), 50,9% en neutro — ninguna diferencia práctica.

**Conclusión honesta**: con el diseño de feature actual (media de sentimiento por ticker/día de Alpha Vantage, suavizada a 3 sesiones), no hay evidencia de que el sentimiento de noticias aporte señal para predecir la dirección a 1 día — ni en correlación simple, ni ponderando por relevancia, ni dentro de un modelo no lineal. Es un resultado negativo consistente con el resto de intentos del proyecto (features técnicas ampliadas, cambio de algoritmo — ver secciones anteriores), no un fallo de la implementación: se probaron varias formas razonables de medir la señal y ninguna apareció.

**Posible explicación (no probada, solo observación)**: al inspeccionar artículos reales de tickers muy cubiertos (ej. NVDA), muchos NO son realmente sobre esa empresa — Alpha Vantage devuelve noticias generales de mercado/sector que solo MENCIONAN al ticker de pasada (relevance_score tan bajo como 0.30), no cobertura dedicada. Filtrar por relevancia alta (>0.6) no cambió el resultado, así que esto no parece ser la causa principal — pero queda como hipótesis descartada, no confirmada del todo.

**Limitación del experimento, por honestidad**: se hizo sobre un subconjunto (13.300 filas, no las 510k completas) y con este sandbox de desarrollo (2 CPUs), no el modelo de producción de verdad. No se espera que reentrenar con el dataset completo cambie la conclusión (la señal buscada es simplemente inexistente en la correlación cruda, que no depende del tamaño de muestra para verse — un r de -0.007 no se vuelve significativo con más filas de la misma distribución), pero es la salvedad honesta a dejar constancia.

**No se ha modificado nada de `model.py`/`features.py`** a raíz de esto — las columnas `news_sentiment_3d`/`news_volume_log` se quedan como están (preparadas, documentadas, pero sin evidencia de que ayuden). Si en el futuro se quiere reintentar, las palancas razonables no probadas aquí serían: una ventana de suavizado distinta a 3 sesiones, un horizonte de predicción más largo (una noticia puede tardar más de 1 día en reflejarse en precio), o una fuente de sentimiento con mejor relevancia dedicada por ticker (Alpha Vantage no lo es, según lo observado).

## Cuadros de mando de noticias y sentimiento (2026-08-13)

Con la investigación de arriba cerrada (sin señal para el modelo), el usuario pidió seguir por el lado puramente de dashboard: visualizar los datos de noticias/sentimiento que ya hay en la base, sin más pretensión que informar — dos páginas nuevas, dejando claro en ambas que esto NO es una señal predictiva demostrada (para no contradecir la investigación de arriba).

**`dashboard/views/sentimiento_por_accion.py`** ("Noticias de la acción") — por ticker: KPIs (nº artículos, tono medio ponderado, días con cobertura), gráfico de tendencia (tono medio en línea + volumen de artículos en barras, eje doble), y listado de titulares recientes con fuente, fecha, relevancia y etiqueta de sentimiento en color. Si el ticker no tiene ninguna noticia todavía (la mayoría, mientras el backfill sigue en marcha — ver "Rediseño de noticias"), muestra un aviso en vez de gráficos vacíos.

**`dashboard/views/sentimiento_del_mercado.py`** ("Sentimiento del mercado") — agregado de TODO el universo: KPI de cobertura del backfill (tickers cubiertos/totales, para que quede claro que sigue en marcha), tendencia de tono medio del mercado (ponderado por volumen de artículos, no media simple de medias — un ticker con 50 artículos pesa más que uno con 1), ranking de los 10 tickers con tono más positivo/negativo (filtrando los que tienen muy pocos artículos, para que uno o dos titulares sueltos no dominen el ranking), y sentimiento medio por sector (`stocks.sector`, últimos 30 días).

**Funciones nuevas en `data_access.py`**: `news_coverage_status()`, `get_daily_sentiment_for_ticker()`, `get_recent_articles()`, `get_market_sentiment_daily()`, `get_ticker_sentiment_ranking()`, `get_sector_sentiment()` — todas solo lectura, reutilizando `news_sentiment_daily` (vista) y `news_articles`/`stocks` (tablas). `SENTIMENT_LABEL_ES`/`SENTIMENT_LABEL_ORDER` traducen las etiquetas de Alpha Vantage (Bullish/Bearish/...) a español, mismo patrón que `FEATURE_LABELS`.

**Decisión de diseño — ranking por ticker usa "últimos N días CON cobertura", no últimos N días de calendario**: con el backfill todavía en marcha, muchos tickers tienen huecos de varios días sin ninguna noticia; si el ranking mirara los últimos 7 días de calendario, esos tickers desaparecerían del todo por mala suerte de timing, no por tener mal sentimiento. `get_ticker_sentiment_ranking()` coge los últimos N días CON al menos un artículo por ticker en su lugar. El sentimiento por sector sí usa días de calendario (últimos 30) porque agrega muchos tickers a la vez — el hueco de uno se diluye entre el resto del sector.

Probado con `AppTest` contra la base real: las 7 páginas del dashboard cargan sin excepciones, incluyendo el caso de un ticker sin ninguna cobertura de noticias todavía (ej. ACN) y uno con cobertura completa (AAPL, NVDA).

**Titulares filtrados por relevancia (2026-08-13)**: el usuario detectó que en "Titulares recientes" (por ticker) salían artículos que en realidad no son sobre esa acción — Alpha Vantage etiqueta un artículo con un ticker en cuanto lo MENCIONA, no solo cuando es el tema principal. Comprobado con datos reales: de los titulares "recientes" de NVDA sin filtrar, la mayoría eran sobre otras empresas (Honeywell, Costco, Vistra, Duke Energy, Apple, Alphabet...) que solo citaban a NVIDIA de pasada, con `relevance_score` bajo (0.55-0.65); los genuinamente centrados en NVDA tenían `relevance_score` cerca de 1.0 (distribución con un salto claro ahí: 27.756 de 96.192 artículos en total valen exactamente 1.0, el resto se reparte sobre todo entre 0.6 y 0.8).

Fix: `get_recent_articles()` gana `min_relevance` (filtro `WHERE relevance_score >= :min_relevance`). La página añade un slider (0.3 a 1.0, por defecto **0.7**) en vez de un umbral fijo — no hay un corte "correcto" único (a 0.7 todavía se cuela algo de ruido para tickers muy mediáticos como NVDA; a 1.0 el listado es más corto pero más limpio), así que se deja ajustable en vez de adivinar un valor perfecto. Verificado con datos reales en los dos extremos del slider (0.3 y 1.0) y sin excepciones.

**Deliberadamente NO se ha tocado** `news_sentiment_daily` (la vista), `attach_news_features()` ni el resto de KPIs/gráficos de esta misma página (tendencia, tono medio) — el usuario pidió el filtro específicamente para el listado de titulares. Si en el futuro se decide que la relevancia también debería pesar en el sentimiento agregado o en la feature del modelo, es un cambio aparte a decidir explícitamente (ver "¿Ayuda el sentimiento de noticias a acertar más?": ya se probó una versión ponderada por relevancia para el modelo y tampoco mostró señal).

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

## Resumen: dashboard unificado (2026-08-13)

El usuario pidió una "primera versión" de dashboard que reúna las gráficas y KPIs más importantes de cada pestaña en una única página, dejando claro que es un v1 a pulir con el tiempo, no la versión final. Decisión: en vez de crear una pestaña nueva, se reescribió `views/inicio.py` (ya era la página por defecto) — evita duplicar navegación y mantiene "la primera pestaña es el resumen" como convención obvia. El título de la pestaña en el router (`Inicio.py`) pasó de "Inicio" a "Resumen" para que el nombre refleje el contenido nuevo.

Secciones incluidas, cada una reutilizando la función de `data_access.py` ya existente de la pestaña de detalle correspondiente (sin duplicar lógica de acceso a datos):
- KPIs generales (igual que antes).
- **Predicciones más recientes**: nueva función `get_latest_predictions_summary(engine)` en `data_access.py` — coge la `date_predicha` más reciente guardada (de cualquier `model_version`) y agrega cuántos tickers predice "sube" vs. "baja" y la probabilidad media. No existía antes ninguna vista agregada de "qué dice el modelo hoy" sin elegir un ticker uno a uno.
- **¿Funciona de verdad? (backtest)**: métricas del modelo vs. los dos baselines, reutilizando `load_latest_model()`.
- **¿Sigue funcionando en producción? (día a día)**: acierto real SOLO de la versión de modelo más reciente (misma regla de oro que `seguimiento_real.py`: nunca mezclar `model_version`), con gráfico compacto de evolución.
- **En qué se fija el modelo**: top 5 features por importancia (en vez de las 16 completas de la pestaña de detalle).
- **Sentimiento del mercado**: tendencia compacta (mismo gráfico que `sentimiento_del_mercado.py` pero sin ranking/sector, que se quedan solo en la pestaña de detalle).

Cada sección enlaza textualmente a su pestaña de detalle para quien quiera profundizar; las pestañas de detalle no se tocaron. Probado con `AppTest` contra la base de datos real: las siete páginas (incluida la nueva `views/inicio.py`) cargan sin excepciones.

## Horizontes de predicción: semana y mes (2026-08-13)

Hasta ahora todo el pipeline (gold.py, model.py, predict.py, track_predictions.py) solo sabía predecir 1 sesión de mercado vista — el selector de horizonte del dashboard ya existía en la interfaz, pero "Semana"/"Mes" siempre mostraban un aviso de "próximamente" (decisión de diseño del 2026-07-31). El usuario pidió activar esa parte del pipeline: extender gold.py/model.py para soportar semana (5 sesiones) y mes (20 sesiones), además del día ya existente.

**Diseño elegido: ampliar `gold_train` con columnas nuevas por horizonte, en vez de multiplicar filas o tablas.** `gold.py` ahora calcula, además de `close_next_day`/`target_up_down` (horizonte 1, nombres sin cambiar por compatibilidad), `close_5d`/`target_up_down_5d` y `close_20d`/`target_up_down_20d` — mismo criterio de siempre (`shift(-N sesiones)` sobre `Close`, nunca "N días naturales", ver `gold.HORIZON_COLUMNS`). A diferencia del horizonte 1 (que solo excluye la última fila de cada ticker), los horizontes largos dejan NULL en las últimas N-1 filas de cada ticker que sí entran en `gold_train` — no hay suficiente futuro todavía para saber si acertarían. `gold_inference` no cambia: las features son las mismas para los tres horizontes, solo cambia qué target se le pide adivinar al modelo, así que no hace falta guardar nada de horizonte ahí.

**Bug real evitado en el cálculo, no solo en la implementación final**: una comparación directa `(close_Nd > close)` con NaN a la izquierda devuelve `False` en pandas/numpy, no `NaN` — sin un `.where()` explícito sobre la máscara de "tiene cierre futuro", las filas sin suficiente futuro se habrían etiquetado silenciosamente como "baja" en vez de quedar excluidas. Se detectó pensando en el caso antes de escribir el código (no en producción), pero se documenta aquí porque es fácil de reintroducir si alguien "simplifica" esta parte más adelante.

**`model.py`** gana `--horizon {1,5,20}` (por defecto 1): selecciona la columna de target de `gold_train`, descarta antes del split las filas sin ese target calculado, y entrena/evalúa exactamente igual que antes para el horizonte elegido — mismas 16 features, mismo split temporal global, mismos baselines obligatorios. El `model_version` guardado incluye el horizonte (`random_forest_h5_20260813172000`) para que puedan convivir varios horizontes a la vez en `models/` sin pisarse — los modelos guardados ANTES de este cambio no llevan `_hN_` en el nombre y se tratan como horizonte 1 por convención (`predict.horizon_from_model_version`).

**`predict.py`** gana `--horizon`: `latest_model_path(horizon=N)` filtra los `.joblib` de ese horizonte; `date_predicha` ahora se calcula con `nth_trading_day(fecha, horizon)` (generaliza `next_trading_day`, horizonte 1) — la sesión de mercado que cae exactamente `horizon` sesiones después de la fecha de `gold_inference`, no siempre "la siguiente".

**`track_predictions.py`** necesitaba el cambio más delicado: antes comparaba SIEMPRE contra "la fila inmediatamente anterior a `date_predicha`" para calcular el resultado real, lo cual es correcto para horizonte 1 (esa fila anterior es justo la sesión en la que se hizo la predicción) pero sería **incorrecto** para horizontes largos (la fila inmediatamente anterior a `date_predicha` NO es la sesión de origen si `date_predicha` está 5 o 20 sesiones más adelante). Corregido: la "sesión de origen" ahora es `pos - horizon` (no `pos - 1`), con `horizon` deducido del propio `model_version` de cada predicción vía `predict.horizon_from_model_version()`.

**Ninguna migración de esquema en `predictions`**: el horizonte no es una columna nueva — vive codificado en el `model_version` (texto libre, ya lo era), así que `predictions` no cambia y no hace falta migrar filas existentes.

**Verificación**: además de compilar cada módulo, se montó una base de datos SQLite sintética completa (2 tickers, ~300 sesiones + 25 sesiones "futuras" simuladas) para probar el ciclo entero gold→model→predict→track_predictions con datos deterministas — los 6 resultados (2 tickers × 3 horizontes) coincidieron exactamente con el cálculo manual esperado, incluyendo que la "sesión de origen" resuelta por `track_predictions.py` cae siempre en la fecha real en la que se hizo cada predicción, no en la sesión inmediatamente anterior a `date_predicha`. Contra la base de datos real: `gold_train` tenía el esquema antiguo (sin las columnas nuevas) — se disparó la migración automática ya existente (drop + recrear vacía, mismo mecanismo que las ampliaciones de 2026-08-06/08) y se repobló con `python gold.py` para los 208 tickers reales (48s, sin fallos). Entrenar un modelo real a horizonte 5/20 con las ~500k filas completas no es viable en este sandbox (2 CPUs, límite de ~45s por comando — mismo límite ya documentado en la investigación de sentimiento del mismo día); se verificó igualmente con datos reales sobre un subconjunto de 15 tickers (~37k filas) que las tres llamadas a `train_and_evaluate()` completan y devuelven métricas razonables en ~10s cada una. **Entrenar los modelos reales de producción a horizonte 5/20 (`python model.py --horizon 5` / `--horizon 20`) queda pendiente en la máquina del usuario** — el dashboard ya está preparado para detectarlos en cuanto existan (si no, avisa con las instrucciones exactas en vez de fingir un resultado).

**Dashboard (`views/predicciones.py`)**: el segmented_control ya no bloquea semana/mes con un aviso fijo de "próximamente" — intenta cargar el modelo más reciente de ese horizonte (`da.load_latest_model(horizon=N)`) y, si no existe todavía (`FileNotFoundError`), muestra el comando exacto para entrenarlo en vez de inventar un resultado. `da.get_latest_prediction(engine, ticker, horizon=N)` filtra las predicciones guardadas por horizonte (deducido del `model_version`, igual que `track_predictions.py`) porque `predictions` no tiene columna de horizonte. El backtest overlay usa la columna de target correspondiente (`target_up_down`/`target_up_down_5d`/`target_up_down_20d`) y descarta primero las filas sin ese target calculado (las últimas sesiones de la ventana elegida, avisado con una `st.caption` explícita en vez de contarlas como acierto o fallo por error).

**Confirmado (2026-08-18): el usuario ejecutó `python model.py --horizon 5` y `--horizon 20` en su máquina real.** `models/` ya tiene `random_forest_h5_20260814181834.joblib` (accuracy 0.521, baseline mayoritario 0.535 — no lo supera) y `random_forest_h20_20260814181859.joblib` (accuracy 0.528, baseline mayoritario 0.535 — tampoco). Los tres horizontes (día 0.505, semana 0.521, mes 0.528) se acercan un poco más al baseline cuanto más largo es el horizonte, pero ninguno lo supera todavía — resultado consistente con el resto del proyecto (ver "Honestidad de resultado"), no una sorpresa. Estos números ya son visibles en la nueva sección "Comparativa por horizonte" de Resumen (ver más abajo).

## Cinco funcionalidades nuevas en Resumen + filtro de sector (2026-08-18)

El usuario, tras revisar una lista de posibles KPIs adicionales, pidió incorporar cinco de ellos: acierto por régimen de volatilidad, comparativa entre horizontes, acierto en el subconjunto de "alta confianza", amplitud de mercado histórica, y filtro por sector en los selectores de ticker. Explícitamente como "v1 dentro del v1" — se añaden a `views/inicio.py` (Resumen) tal cual, sin intentar todavía consolidarlo todo en una única visualización definitiva (eso queda para una iteración posterior).

**Nuevas funciones en `dashboard/data_access.py`** (todas de solo lectura, reutilizando `gold_train`/`predictions`/`stocks`, sin ninguna fuente de datos nueva):

- `_test_set_predictions(engine, bundle)` (helper interno): aplica el modelo cargado sobre las filas de `gold_train` del **periodo de test oficial del propio bundle** (`date >= bundle["test_date_min"]`) — nunca sobre train, para no dar una imagen artificialmente buena. Devuelve el DataFrame con `pred`/`proba_up`/`acierto` ya calculados; lo reutilizan las dos funciones siguientes para no duplicar la misma lógica de evaluación dos veces.
- `get_accuracy_by_volatility(engine, bundle)`: acierto sobre el test oficial, agrupado en **terciles** de `volatility_10d` (Baja/Media/Alta) — terciles sobre el propio test set, no un umbral fijo a mano, para que los tres grupos tengan tamaño comparable. Resultado real (modelo día, test 2026-01-20→2026-07-17): Baja 50,9%, Media 51,4%, Alta 49,3% — diferencias pequeñas, sin un patrón claro de "acierta mejor en calma".
- `get_confidence_accuracy(engine, bundle, threshold)`: compara el acierto de TODAS las predicciones del test frente al subconjunto de "alta confianza" (probabilidad de la clase predicha ≥ `threshold`). **Resultado real, consistente en todos los umbrales probados (0.52 a 0.65): la accuracy del subconjunto de alta confianza es SIEMPRE más baja que la general** (p. ej. a 0.6: 44,6% vs. 50,6% general, sobre solo 101 de 27.030 filas) — el modelo no discrimina mejor cuando está más "seguro" de sí mismo, más bien al contrario. Se muestra tal cual en el dashboard (con un `delta` en rojo si es negativo), sin suavizar el resultado — mismo criterio de honestidad que el resto del proyecto. Con Random Forest y una señal débil, además, muy pocas filas superan un umbral alto (0,37% a partir de 0.6), así que hay que leer el porcentaje de alta confianza con el tamaño de muestra a la vista, no solo la cifra de acierto.
- `get_horizon_comparison(engine)`: backtest (modelo vs. ambos baselines) de la versión más reciente de cada horizonte entrenado — fila con `disponible=False` para los que no tienen modelo, nunca una fila inventada.
- `get_market_breadth_history(engine, horizon, days)`: evolución histórica del % de tickers que el modelo predijo "sube" cada sesión (horizonte día, últimos 90 días) — a diferencia del acierto real, aquí SÍ se pueden combinar varias `model_version` en el mismo cálculo (no es una métrica de rendimiento, solo la inclinación del modelo sobre el mercado), filtrando cada predicción por su horizonte deducido vía `predict.horizon_from_model_version`.
- `get_tickers_by_sector(engine)`: tickers con datos reales (mismo criterio que `list_tickers`) junto con su `sector` — base del nuevo filtro de sector.

**Secciones nuevas en Resumen** (`views/inicio.py`), intercaladas entre las ya existentes: "Amplitud de mercado (histórico)" tras "Predicciones más recientes"; "Comparativa por horizonte" tras "¿Funciona de verdad?"; "¿Cuándo acierta más el modelo?" (volatilidad + confianza, dos paneles lado a lado) tras "En qué se fija el modelo". El slider de umbral de confianza usa el mismo patrón ya establecido en "Noticias de la acción" (rango acotado con valor por defecto razonable, no un único umbral "correcto" adivinado) — rango 0,52-0,70, por defecto 0,55 (a partir de 0.6 la muestra de "alta confianza" ya es demasiado pequeña para ser representativa por defecto).

**Filtro por sector** (`views/predicciones.py`, `views/sentimiento_por_accion.py`): selector "Sector" (`stocks.sector`, vía `get_tickers_by_sector`) que filtra la lista de tickers del selector "Ticker" — "Todos" por defecto, sin romper el comportamiento existente. No se ha tocado ninguna otra pestaña (importancia de features y rendimiento del modelo son globales, no por ticker; día a día filtra por `model_version`, no por ticker).

Probado con `AppTest` contra la base de datos real: las siete páginas cargan sin excepciones, incluyendo cambiar el sector en "Predicciones" (verificado que el selector de ticker se actualiza a solo los tickers de ese sector) y mover el slider de confianza en "Resumen".

## Disparador adicional en Task Scheduler: al iniciar sesión (2026-08-19)

El usuario preguntó si podía hacer que `Stocker_Noticias_Diarias`/`Stocker_Pipeline_Diario` se ejecutaran cada vez que enciende el portátil, no solo a la hora fija programada. Fuera del alcance de esta herramienta (no hay acceso al Task Scheduler real del usuario desde aquí) — se le dieron los comandos de PowerShell exactos para que los ejecute él (ver `src/README.md`, "Disparador adicional: ejecutar también al iniciar sesión"). Decisión, tras preguntar explícitamente: **añadir** el disparador "al iniciar sesión" (con 2 min de margen) a las dos tareas, sin quitar el de hora fija — más robusto que sustituirlo por completo, porque si el usuario deja el portátil encendido varios días sin reiniciar sesión, la hora fija sigue cubriendo esa ejecución diaria.

**Confirmado en la máquina real del usuario**: `Get-ScheduledTask ... | Select -ExpandProperty Triggers` sobre las dos tareas muestra el `CalendarTrigger` original (`DaysInterval: 1`) más un `LogonTrigger` nuevo (`Delay: PT2M`) en cada una — el PowerShell se ejecutó sin errores y quedó tal como se diseñó.

## Prioridad de noticias para tickers relevantes (2026-08-25)

Petición del usuario: que las acciones "más relevantes del mercado" (una
muestra de entre 25 y 100) se actualicen en `news.py` con más frecuencia
que el resto, en vez de quedar sujetas solo al muestreo aleatorio uniforme
de `run_daily_update()` (ver "Rediseño de noticias: 1 ticker por
llamada" más abajo) — que da a cada uno de los 208 tickers la misma
probabilidad, ~1 revisión cada 8-9 días de media.

**Señal de relevancia usada**: no existe ningún campo de capitalización
bursátil en `stocks` para ordenar "de verdad" por tamaño de mercado, y no
se quiso meter una fuente de datos nueva solo para esto. Se reutiliza el
orden ya existente de `config.TICKERS`, que el propio usuario dejó
empezando por los mega-caps (NVDA, AAPL, MSFT, AMZN, GOOGL, GOOG, AVGO,
META, TSLA, LLY...) — los primeros `NEWS_PRIORITY_TICKERS_COUNT` (50,
punto intermedio del rango 25-100 pedido) pasan a ser
`config.NEWS_PRIORITY_TICKERS`.

**Mecanismo**: dentro de `NEWS_DAILY_CALL_BUDGET` (25/día, límite del free
tier de Alpha Vantage — el presupuesto TOTAL no cambia, solo se reparte
distinto), `NEWS_PRIORITY_DAILY_SHARE=0.8` (20 llamadas/día) se reserva
para recorrer `NEWS_PRIORITY_TICKERS` en **ronda determinista** — sin
tabla de cursor nueva: el offset del día sale de
`date.today().toordinal()` (un entero que ya avanza uno por día de
calendario), así que el ciclo completo de los 50 tarda
`ceil(50/20) = 3` días, sin saltos ni repeticiones antes de completar la
vuelta. Verificado con un script que simula 4 días consecutivos
(monkeypatchando `fetch_news_batch`/`dt.date.today`): los lotes
prioritarios cubren exactamente 0-49 en 3 días (día 0: lotes 10-29, día
1: 30-49, día 2: 0-19 con wrap) antes de repetir. Las 5 llamadas
restantes del presupuesto siguen el muestreo aleatorio de siempre, pero
ahora solo sobre los tickers NO prioritarios.

**Trade-off real y aceptado, no escondido**: como el presupuesto total no
cambia, priorizar a unos implica desatender más a los otros. Los 158
tickers no prioritarios pasan de revisarse cada ~8-9 días de media a cada
~30 días (158/5). Se acepta porque el usuario pidió explícitamente
priorizar relevancia sobre cobertura uniforme — si en el futuro hace
falta más cobertura general, la palanca es `NEWS_PRIORITY_DAILY_SHARE`
(baja el share) o `NEWS_PRIORITY_TICKERS_COUNT` (baja el tamaño del set
prioritario), ambas en `config.py`.

Cambios: `config.py` (`NEWS_PRIORITY_TICKERS_COUNT`,
`NEWS_PRIORITY_TICKERS`, `NEWS_PRIORITY_DAILY_SHARE`) y
`news.run_daily_update()` reescrita para partir el presupuesto y añadir
la etiqueta prioritario/aleatorio en los logs. `run_backfill()` no se
toca — el backfill histórico ya recorre los 208 tickers por orden, sin
distinción de prioridad, porque es un proceso de una sola vez.

## Dashboard unificado (mockup) (2026-08-19 → implementado 2026-08-25)

Tras los 5 KPIs nuevos añadidos a "Resumen" (ver más arriba), el usuario
pidió un mockup visual de estilo SaaS (compartió dos capturas de
referencia: un dashboard operativo de atención al cliente y un informe
ejecutivo comercial, ambos con tarjetas de KPI + gráficos + paneles de
alertas/insights) antes de escribir código. Iterado dos veces con
`mcp__visualize__show_widget` hasta aprobar: barra lateral de filtros,
texto dinámico según selección, tabla de noticias de una línea por
acción con color de sentimiento, y un medidor en semicírculo con el
sentimiento GENERAL del mercado (de TODAS las acciones, no filtrado).

**Decisiones de alcance, preguntadas explícitamente antes de tocar
código (2026-08-25)**:
- Página **nueva y separada** ("Dashboard"), no sustituye a "Resumen" —
  las dos conviven; "Resumen" sigue siendo la lista de KPIs por sección,
  "Dashboard" es la reinterpretación visual recentrada en una acción.
- El selector de acción de la barra lateral **recoloca TODO el
  dashboard** (KPIs de cabecera, gráfico, noticias, texto de insights),
  no solo la tabla de noticias — con el matiz de que el medidor de
  sentimiento del mercado se queda siempre global (petición explícita
  del usuario: "de TODAS las acciones").
- Las páginas de detalle existentes (Predicciones, Día a día, ¿Funciona
  de verdad?, En qué se fija, Noticias de la acción, Sentimiento del
  mercado) **se mantienen intactas**, enlazadas desde el pie de
  "Dashboard" (`st.page_link`) — mismo patrón que ya usaba "Resumen".
- Horizonte: **uno a la vez con selector** (día/semana/mes), igual que
  "Predicciones", no los tres en paralelo.
- Responsividad: no hay CSS a mano para móvil — se apoya en que
  `st.columns` de Streamlit ya apila verticalmente en pantallas
  estrechas de forma nativa; se mantuvieron como mucho 4 columnas por
  fila para que ese apilado se lea bien.

**Piezas nuevas**:
- `dashboard/views/dashboard.py` (nuevo) — KPIs de cabecera (predicción,
  probabilidad, último cierre + variación, acierto del backtest oficial)
  recentrados en la acción elegida; gráfico de precio con aciertos/fallos
  del backtest + predicción destacada (misma lógica que "Predicciones",
  condensada); medidor de sentimiento del mercado; tabla de noticias de
  una línea por acción (título/fuente-fecha/pill de sentimiento, HTML
  escapado con `html.escape` porque los títulos vienen de Alpha Vantage,
  fuente externa); párrafo de "Lectura rápida" con texto generado a
  partir de los mismos datos (predicción, acierto, tono de noticias de
  la acción, ambiente general del mercado); enlaces a las páginas de
  detalle.
- `data_access.get_market_sentiment_gauge(engine, days=7)` (nuevo) —
  colapsa `get_market_sentiment_daily` (ya existente) a un único escalar
  ponderado sobre los últimos 7 días de calendario con cobertura, para
  alimentar el medidor. `data_access.sentiment_scalar_label(score)`
  (nuevo, pública) — mismos umbrales que `news._sentiment_label` mapeados
  a español; se duplica a propósito en vez de importar `news.py` desde
  `data_access.py` (capas de ingesta y lectura deliberadamente
  desacopladas) — si esos umbrales cambian en `news.py`, hay que
  replicarlos aquí también.
- Medidor semicircular construido a mano en SVG (`_sentiment_gauge_svg`
  dentro de `views/dashboard.py`), no con `go.Indicator` de Plotly — el
  gauge nativo de Plotly no da un semicírculo real (siempre un arco de
  ~270°), y el mockup aprobado pedía la forma exacta de semicírculo con
  degradado rojo→gris→verde y aguja.

**Verificado** con `streamlit.testing.v1.AppTest` contra la base de datos
real: cargando la app completa desde `Inicio.py` y navegando con
`switch_page("views/dashboard.py")` (necesario para que `st.page_link`
resuelva las rutas — probarlo con `AppTest.from_file()` directo sobre el
archivo de la vista falla con `KeyError: 'url_pathname'`, error del
arnés de pruebas al faltar el contexto de navegación completo, no un bug
real). Probado: cambio de sector, cambio de ticker (incluidos varios sin
mucha cobertura de noticias), los tres horizontes, extremos del slider de
meses — sin excepciones; el SVG del medidor y la tabla HTML de noticias
aparecen renderizados en la salida.

**Recortado el mismo día, tras el primer feedback del usuario sobre el
boceto**: "no me disgusta, eso sí... elimina la Tendencia del mercado y
el enlace y todo su apartado respectivo de ¿Funciona de verdad?". Se
quitaron dos piezas completas de `views/dashboard.py`:
- El medidor de sentimiento general del mercado (sección, función
  `_sentiment_gauge_svg`, la consulta `_market_gauge()` y su mención en
  el enlace de pie de página "Sentimiento del mercado →" y en el párrafo
  de "Lectura rápida"). Motivo, con sentido más allá de "el usuario lo
  pidió": era la única pieza del dashboard que NO se recentraba en la
  acción elegida, rompía la idea central de la página.
- El KPI "Acierto del modelo (test oficial)" de la cabecera, su frase en
  "Lectura rápida" y el enlace de pie de página "¿Funciona de verdad?
  →" — quedaba redundante con esa página dedicada (sigue accesible desde
  la navegación superior de siempre, solo se quitó el atajo duplicado
  desde Dashboard). El gráfico de precio conserva los marcadores de
  acierto/fallo del backtest — solo se quitó el número agregado y el
  texto que lo repetía.

La fila de KPIs de cabecera pasa de 4 a 3 columnas (Predicción,
Probabilidad, Último cierre) y el bloque de noticias pasa a ocupar el
ancho completo (antes compartía fila con el medidor). El pie de página
se queda con 2 enlaces (Predicciones, Noticias de la acción) en vez de
4. Reverificado con `AppTest` tras el recorte: 3 métricas, 2
`page_link`, sin excepciones en los tres horizontes ni al cambiar de
ticker/sector.

## Roadmap v1 (MVP para publicar) (2026-08-27)

El usuario quiere encarar el proyecto hacia un "producto mínimo viable"
publicable en un plazo corto, y pidió analizar el proyecto, quitar
pestañas sin valor para una v1, y un roadmap de lo que falta. Dado que
"publicar" es ambiguo (¿desplegado en internet? ¿solo repo pulido?) y
que decidir qué se queda fuera de una primera versión es una decisión de
producto de peso, se preguntó explícitamente antes de tocar nada — el
usuario acababa de pedir precisamente eso: "SIEMPRE ante cualquier duda
[...] pregúntame [...] antes de que se hagan más grandes en un futuro"
(guardado en memoria del agente, no solo aquí).

**Decisiones confirmadas por el usuario (2026-08-27)**:
- **Qué significa "publicar"**: demo desplegada con **datos congelados**
  (una foto de la base de datos en el momento de publicar, sin
  actualización automática en la nube) — no un producto vivo con
  pipeline corriendo en la nube a diario. Eso simplifica mucho el
  alcance: no hace falta resolver scheduling en la nube ni exponer
  `ALPHA_VANTAGE_API_KEY` en el despliegue (el dashboard es de solo
  lectura sobre `stocker.db`/`models/`, nunca llama a ninguna API en
  tiempo real — verificado revisando `data_access.py`, no importa
  `requests` ni nada de `news.py`).
- **"Resumen" fuera de la navegación del v1**: `views/dashboard.py` pasa
  a ser la página de entrada (`default=True`); `views/inicio.py` se
  queda como archivo sin registrar en `Inicio.py` (no se borra, se
  recupera con una línea si hace falta en el futuro).
- **"Noticias de la acción" y "Sentimiento del mercado" fuera del v1**:
  dependen del backfill de Alpha Vantage (~9 días desde cero por el
  límite gratuito de 25 llamadas/día) y CONTEXTO.md ya documenta que esa
  señal no ayuda a predecir el precio — no sostienen el valor central
  del proyecto. Mismo tratamiento: archivos intactos, solo
  desregistrados de `Inicio.py`.
- **Plazo**: "esta semana" — roadmap deliberadamente ajustado, se
  posponen pulidos no críticos.

**Navegación del v1 resultante** (`dashboard/Inicio.py`, 5 páginas):
Dashboard (entrada) → Predicciones → En qué se fija → ¿Funciona de
verdad? → Día a día. Verificado con `AppTest`: las 5 cargan sin
excepción, `Dashboard` es la página por defecto, y su enlace de pie de
página a "Predicciones" resuelve bien (el enlace a "Noticias de la
acción" se quitó de `views/dashboard.py` porque `st.page_link` no
resuelve una página no registrada).

**Hallazgo clave de la investigación, con impacto directo en el
roadmap**: `data/stocker.db` pesa 254MB y `models/` 55MB — ninguno de
los dos se versiona en git (`.gitignore` los excluye) y además 254MB
supera el límite de 100MB por archivo de GitHub, así que ni siquiera
cabría en un repo normal aunque se quisiera. Para una demo desplegada
con datos congelados, hay que decidir CÓMO viajan esos ~309MB hasta el
sitio donde se aloje la app — esto todavía no está resuelto, es la
pieza central pendiente del roadmap (ver `docs/ROADMAP_MVP.md` para el
detalle y las opciones planteadas al usuario).

**Decisión sobre el alojamiento de los datos (2026-08-27)**: en vez de
Git LFS o descarga externa, se recorta el universo de tickers para que
la base de datos quepa directamente en un repo normal de GitHub. Se
reutiliza `config.NEWS_PRIORITY_TICKERS` (los 50 tickers más relevantes,
ya definidos para la rotación prioritaria de noticias, ver "Prioridad de
noticias para tickers relevantes") como el subconjunto de la demo — no
se inventa un criterio de relevancia nuevo. Estimado a partir de filas
reales (daily_prices: 2497 filas/ticker de media, gold_train: 2463,
news_articles: 932): con 50 tickers la base de datos de la demo pesaría
~60MB (254MB / 208 × 50), muy por debajo del límite de 100MB de GitHub.
Los modelos NO necesitan recortarse ni reentrenarse: no usan el ticker
como feature (ver `model.py`), así que los `.joblib` ya entrenados sobre
el universo completo (~6-7MB cada uno) sirven igual para predecir sobre
el subconjunto de la demo.

El roadmap completo, accionable y con checkboxes vive en
[`docs/ROADMAP_MVP.md`](docs/ROADMAP_MVP.md) — no se duplica aquí para
no tener dos fuentes de verdad; este apartado es el resumen de las
decisiones y su porqué.

**Fase 1 completada (2026-08-27)**: `src/export_demo_db.py` (nuevo) copia
`stocks`/`daily_prices`/`gold_train`/`gold_inference`/`predictions`/
`news_articles` de `data/stocker.db` filtrando por
`NEWS_PRIORITY_TICKERS`, a `data/stocker_demo.db`. Bug real encontrado y
corregido durante la verificación: la primera versión usaba
`metadata.create_all(dst_engine, tables=_TABLES)`, que crea tablas físicas
pero NO la vista `news_sentiment_daily` (SQL crudo, fuera de `metadata`)
— `AppTest` contra la base generada falló con `OperationalError: no such
table: news_sentiment_daily` en cuanto el Dashboard intentó leer
sentimiento de noticias. Corregido usando `dbmod.init_db(dst_engine)`
(crea todo el esquema real, incluida la vista) en vez de un
`create_all` parcial. Ejecutado contra la base real: 61.0MB para 50
tickers (daily_prices: 123.658 filas, gold_train: 121.958, predictions:
750, news_articles: 49.381) — dentro de lo estimado.

`config.DB_PATH`/`config.MODELS_DIR` ahora aceptan override por
`STOCKER_DB_PATH`/`STOCKER_MODELS_DIR` (si no están definidas, idéntico
comportamiento de siempre — ningún `.bat` programado se entera del
cambio). Los 3 `.joblib` elegidos para la demo (horizonte día:
`random_forest_20260810181458`, semana: `random_forest_h5_20260826165001`,
mes: `random_forest_h20_20260826165119` — los más recientes de cada
horizonte en el momento de publicar) se copiaron literalmente a
`models_demo/` sin reentrenar: el modelo no usa el ticker como feature,
así que sirve igual sobre el subconjunto de 50 tickers que sobre los 208.

`.gitignore` gana dos excepciones puntuales (`!data/stocker_demo.db`,
`!/models_demo/` + `!/models_demo/*.joblib`) sin afectar a `data/stocker.db`
ni a `/models/`, que se siguen ignorando — verificado con `git add -n`
(los 4 ficheros de la demo se añadirían; la base real y `models/` no).

Dashboard gana un aviso "Datos actualizados hasta: {fecha}" (nueva
`data_access.get_latest_data_date()`, máximo de `daily_prices.date`) — es
la señal honesta de que la demo publicada es una foto fija: si esa fecha
deja de avanzar, no es la versión en vivo. No hace falta detectar "modo
demo" en ningún sitio, la propia fecha ya lo comunica.

**Quitado el 2026-08-28**: el usuario pidió eliminar este aviso del
Dashboard. `data_access.get_latest_data_date()` se deja tal cual (no se
usa en ningún sitio ahora mismo, pero no molesta) por si se quiere volver
a mostrar en el futuro — solo se quitó la llamada y el `st.caption` de
`views/dashboard.py`.

Todo verificado con `AppTest` en dos configuraciones: contra
`data/stocker.db`/`models/` reales (sin variables de entorno) y contra
`data/stocker_demo.db`/`models_demo/` (con `STOCKER_DB_PATH`/
`STOCKER_MODELS_DIR`) — 5 páginas cargan sin excepción en ambos casos,
selector de ticker con 50 opciones en la demo, los tres horizontes
funcionan.

Pendiente (Fase 1, acciones que requieren cuentas/decisiones del
usuario, no ejecutables desde aquí): confirmar plataforma de despliegue
(asumido Streamlit Community Cloud), público/privado del repo, y el
`git add`/`commit`/`push` real de `stocker_demo.db` + `models_demo/` —
deliberadamente no automatizado, publica contenido en su GitHub.

## Experimento: modelo de un solo ticker vs. pooled (2026-08-28)

El usuario preguntó cómo se entrena el modelo real (pooled vs. por
ticker) y pidió una prueba pequeña: entrenar SOLO con el histórico de
NVDA y comparar. Script `src/experiment_single_ticker.py` (BORRADO el
2026-08-28, mismo día que se escribió — el usuario pidió eliminar la
versión de desarrollo una vez respondida la pregunta; no dejaba ningún
artefacto persistente, así que borrarlo no afecta a nada en producción,
ver más abajo) — deliberadamente NO tocaba `models/` (no guardaba
`.joblib`, contaminaría `predict.latest_model_path()`) ni `predictions`
(no persistía nada en la base de datos): era un experimento de
comparación, no una alternativa de
producción. Reutilizaba `model.train_and_evaluate`/`build_feature_matrix`/
`print_report` tal cual para que la comparación fuera justa (mismas
features, mismo criterio de split, mismos baselines).

Además de entrenar y evaluar el modelo de un solo ticker, evaluaba
también el modelo pooled ya entrenado sobre las MISMAS filas de test de
ese ticker (mismo periodo, mismo target) — comparación sobre exactamente
las mismas filas, no dos test sets distintos.

**Bug real encontrado y corregido al verificarlo**: `df["date"]` de
`gold_train` son objetos `datetime.date` (tal cual los devuelve
sqlite3), no `pd.Timestamp` — comparar con un `pd.Timestamp` construido
a mano lanza `TypeError: Cannot compare Timestamp with datetime.date`.
Corregido usando `dt.date.fromisoformat(...)` en vez de `pd.Timestamp(...)`
para el corte de fecha.

**Resultado real, horizonte día, NVDA** (126 filas de test,
2026-02-27 a 2026-08-27): el modelo de SOLO NVDA (2.502 filas propias)
obtiene acc=0.516 sobre sus propias 126 filas de test; el modelo POOLED
(entrenado con 512.958 filas de 208 tickers) obtiene acc=0.444 sobre
esas MISMAS 126 filas. En esta ventana concreta, el modelo aislado le
gana al pooled en el propio terreno de NVDA — aunque ninguno de los dos
supera de forma consistente a los baselines (mayoritario 0.508,
persistencia 0.524), y la muestra es pequeña (126 filas) para sacar una
conclusión fuerte. Resultado real, reportado tal cual, no una prueba
definitiva de que "un modelo por ticker es mejor" — haría falta repetir
en varios tickers y ventanas para confirmar el patrón (no hecho todavía,
fuera del alcance de "una prueba pequeña").

## NVDA como acción principal por defecto (2026-08-28)

Los tres selectores de ticker del dashboard (`views/dashboard.py`,
`views/predicciones.py`, `views/sentimiento_por_accion.py`) usaban
`index=0` a secas sobre listas ordenadas ALFABÉTICAMENTE
(`list_tickers()`/`get_tickers_by_sector()`, ambas `ORDER BY ticker`) —
por eso salía AAPL seleccionado por defecto, sin que nadie lo hubiera
decidido así, solo por ser el primero en orden alfabético.
`config.TICKERS` ya empezaba por NVDA (y `config.DEFAULT_TICKER =
TICKERS[0]` ya era NVDA), pero eso nunca llegó a los selectores del
dashboard porque leen de `daily_prices`/`stocks` vía SQL ordenado, no de
la lista de config.

Nueva función `data_access.default_ticker_index(tickers)`: devuelve el
índice de `config.DEFAULT_TICKER` si está en la lista dada, si no cae al
primero (mismo comportamiento que antes cuando un filtro de sector
excluye a NVDA). Reutilizada en los tres selectores en vez de duplicar la
lógica. Verificado con `AppTest`: "Dashboard" y "Predicciones" arrancan
con NVDA seleccionado.

## Logos de empresa en el dashboard (2026-08-28)

Petición del usuario: hacer el dashboard más visual incorporando el logo
de cada ticker. Investigado antes de tocar código (información
"presente", no del entrenamiento — verificado con búsqueda web): la
opción obvia hace un tiempo, Clearbit Logo API, **cerró el 8 de
diciembre de 2025** — las peticiones a `logo.clearbit.com` ya no
funcionan. El propio equipo de Clearbit/HubSpot recomienda **Logo.dev**
como sucesor oficial (mismo equipo, migración directa).

Opciones evaluadas y presentadas al usuario:
- **Logo.dev** (elegida): busca logo directamente por ticker
  (`img.logo.dev/ticker/{TICKER}?token=...`), 70.000+ tickers en 60+
  mercados, capa gratuita de 500K peticiones/mes. Requiere crear una
  cuenta gratuita (sin tarjeta) para conseguir un "publishable key".
- **AllInvestView Ticker Logos** (descartada): sin cuenta ni clave, pero
  exige un enlace de atribución visible ("Logos by AllInvestView") en
  el dashboard y funciona por DOMINIO de empresa, no por ticker
  directamente (habría que guardar el dominio de cada ticker, p. ej.
  ampliando `enrich_stocks.py` con `Ticker.info['website']`).

El usuario prefirió explícitamente crear una cuenta antes que mostrar
una atribución de terceros en su proyecto de máster.

**Implementación**: `config.LOGO_DEV_TOKEN` (env var, `.env.example`
actualizado) — es una "publishable key" pensada para ir en una URL de
`<img>`, no un secreto de servidor, pero se lee de config igual que
`ALPHA_VANTAGE_API_KEY`, nunca hardcodeada. Nueva
`data_access.ticker_logo_url(ticker)`: devuelve la URL o `None` si no
hay token configurado — SIN token, el dashboard funciona exactamente
igual que antes, simplemente sin logos (no rompe nada, no hace falta que
el usuario configure esto para poder seguir trabajando). Logo mostrado
en la cabecera de "Dashboard" (48px) y "Predicciones" (40px), junto al
nombre de la empresa — las otras páginas no lo llevan, según lo pedido.

Verificado con `AppTest` en dos configuraciones: sin `LOGO_DEV_TOKEN`
(camino por defecto, sin excepción, sin imagen) y con un token de
prueba (`st.image` se renderiza sin excepción — la validación de que la
imagen REAL carga bien depende del token real del usuario, no
verificable desde aquí).

**Actualizado el mismo día** — el usuario ya creó la cuenta y pegó el
prompt de configuración oficial de Logo.dev (con su publishable key
real). Se verificó contra la documentación oficial
(`logo.dev/docs/logo-images/get`, fetch real, no de memoria) el esquema
exacto de parámetros — la construcción de URL que ya existía
(`img.logo.dev/ticker/{TICKER}?token=...`) era correcta, pero se
amplió con los parámetros documentados que mejoran la calidad visual:
- `format=png` + `theme=light`: el formato por defecto (`jpg`) no tiene
  transparencia (el logo queda en una caja blanca); con PNG transparente
  + `theme=light`, los logos claros se invierten para seguir siendo
  visibles sobre el fondo blanco del dashboard (tip explícito de la
  documentación oficial).
- `retina=true`: doble resolución real para pantallas de alta densidad.
- `fallback` se deja en su valor por defecto (`monogram`, no `404`): si
  Logo.dev no tiene el logo de un ticker, devuelve un monograma en vez
  de fallar — así `st.image()` nunca muestra un icono de imagen rota
  (Streamlit no tiene un `onerror` de HTML al que engancharse).

`data_access.ticker_logo_url(ticker, size=48)` ahora acepta `size`
explícito (Dashboard pide 48, Predicciones 40, coincidiendo con el
ancho real del `st.image()` de cada página). El token real ya está en
`.env` local (no en `.env.example`, que sigue sin secretos) y en
`config.LOGO_DEV_TOKEN`. Verificado con `AppTest` cargando el `.env`
real: ambas páginas renderizan sin excepción con la imagen presente.
Pendiente del usuario: añadir la misma variable a los "Secrets" de la
plataforma de despliegue cuando publique la demo.

## Cabecera de marca en Dashboard (2026-08-28)

Petición del usuario: quitar el `st.title("Dashboard")` de la página de
entrada y sustituirlo por una cabecera de marca — "STOCKER" arriba a la
izquierda, con subtítulo dinámico debajo (logo del ticker + nombre de la
empresa + `#TICKER`, en gris claro).

Cambios en `dashboard/views/dashboard.py`:
- Se quitó `st.title("Dashboard")` y su caption descriptiva de debajo
  (eran redundantes con la pestaña de navegación, que ya dice
  "Dashboard").
- El antiguo bloque de cabecera del ticker (columnas con logo + `###
  Nombre · \`TICKER\``) se sustituyó por: `st.title("STOCKER")` seguido
  de un bloque HTML (`st.markdown(..., unsafe_allow_html=True)`, mismo
  patrón ya usado en la tabla de noticias de esta página) con el logo
  (`<img>`, 26px) y un `<span>` en `color:#9CA3AF` (el mismo gris que ya
  se usaba para "Neutral" en el sentimiento) con el nombre de la empresa
  y `#TICKER` en negrita — p. ej. "Nvidia Corporation · **#NVDA**".
- Como el subtítulo depende del ticker elegido en la barra lateral, la
  cabecera ahora se pinta DESPUÉS de leer la selección de la barra
  lateral, no antes — se movió el bloque de título de la parte de arriba
  del script a justo después de `meta = da.get_ticker_metadata(...)`.
- El caption de sector/país y el aviso de fecha de datos (demo
  congelada) se conservan intactos, ahora justo debajo del subtítulo.
- `html.escape()` se sigue aplicando al nombre de la empresa antes de
  inyectarlo en el HTML (mismo cuidado que ya se aplicaba en la tabla de
  noticias).

No se tocó `views/predicciones.py` — esta petición nombraba
específicamente "al entrar al dashboard", así que su cabecera (logo +
caption de una línea) se dejó como estaba.

Verificado con `AppTest` contra la base de datos real: sin excepciones,
`at.title[0].value == "STOCKER"`, el bloque markdown con el logo/nombre/
`#NVDA` presente, y el orden de captions correcto (sector/país → fecha
de datos → resto de la página).

**Actualizado el mismo día**: el `st.title("STOCKER")` se volvió a
quitar — el usuario pidió eliminarlo por redundante con el wordmark
"Stocker" de la barra de navegación (`ui.render_logo()`). La cabecera de
Dashboard quedó solo con el subtítulo dinámico (logo + nombre + ticker).

## Experimento de un solo ticker: eliminado (2026-08-28)

El usuario pidió borrar `src/experiment_single_ticker.py` una vez
respondida la pregunta que lo motivó (ver sección de arriba). No dejaba
ningún artefacto persistente (no `.joblib`, no filas en `predictions`),
así que borrarlo no afecta a nada en producción — solo se pierde el
script en sí; el resultado y el bug encontrado quedan documentados en la
sección de arriba para no perder el conocimiento.

## Identidad de marca: logos del artifact de claude.ai (2026-08-28)

El usuario compartió un artifact de claude.ai (`claude.ai/code/artifact/
856fecea-...`) con el logo real de Stocker — no pude abrirlo directamente
(la extensión Claude in Chrome no estaba conectada en esta sesión, y el
fetch normal de esa URL solo devuelve la carcasa vacía sin el contenido,
que se carga por JavaScript), así que el usuario subió los 4 ficheros
generados por ese artifact directamente:
- `stocker-k-icon.svg` — icono suelto: una "K" donde el trazo diagonal es
  un gráfico de líneas con dos tramos (verde ascendente, rojo
  descendente, con puntos marcando "sesiones"), fondo transparente.
- `stocker-wordmark.html` — "STOCKER" completo con el icono sustituyendo
  la letra K, tipografía Space Grotesk (Google Fonts) en negrita.
- `stocker-app-icon-dark.svg` / `stocker-app-icon-light.svg` — el mismo
  icono dentro de un cuadrado redondeado (200×200), en dos variantes de
  fondo (`dark` = fondo azul marino `#0f172a`, pensada para tabs/fondos
  claros; `light` = fondo blanco, pensada para fondos oscuros).

Petición del usuario: "usa estos artifacts para usar los logos como
imagen de marca en toda la app". Decisiones tomadas:

1. **Los 4 SVG originales se guardaron tal cual** en el nuevo directorio
   `dashboard/assets/` (más el HTML del wordmark, sin usar, solo de
   referencia) — no se modificó ningún path ni color de los que trajo el
   usuario.
2. **El wordmark NO se pudo usar tal cual** porque depende de Space
   Grotesk vía `<link>` a Google Fonts, y `st.logo()` de Streamlit pinta
   una imagen (SVG o raster), no ejecuta HTML/CSS — un `@font-face`
   externo dentro de un SVG usado como imagen no es fiable entre
   navegadores. Tampoco se pudo descargar el fichero de la fuente para
   incrustarlo en base64 (la política de esta sesión prohíbe usar
   `curl`/`requests` en la shell para saltarse las restricciones de las
   herramientas de navegación web cuando el fetch normal no sirve para
   binarios). Solución: `dashboard/assets/generate_wordmark.py` convierte
   el texto "STOC" + "ER" a CONTORNOS vectoriales (paths SVG) con
   `fonttools`, usando **Poppins Bold** como sustituta de Space Grotesk
   (geométrica, proporciones parecidas, ya instalada localmente en el
   entorno — no hubo que descargar nada). El resultado
   (`stocker_wordmark.svg`) se ve exactamente igual en cualquier
   navegador porque ya no depende de ninguna fuente instalada, ni de
   conexión a internet — es el mismo criterio que usaría cualquier editor
   de logos (los logotipos siempre se entregan como contornos, nunca como
   texto editable, precisamente por esto). Verificado renderizando a PNG
   con `cairosvg` antes de integrarlo (además de servir de verificación
   visual, confirmó que SÍ hacía falta este paso: la primera versión con
   `@font-face` embebido en base64 no se renderizó con la fuente correcta
   ni siquiera en ese renderizador de prueba, la de contornos sí).
3. **`ui.render_logo()`** ahora usa `st.logo(image=stocker_wordmark.svg,
   icon_image=stocker_k_icon.svg)` — el wordmark completo cuando la barra
   lateral está abierta, y el icono "K" suelto cuando está colapsada
   (mismo patrón logo-completo/icono-compacto de cualquier app SaaS).
4. **Favicon** (`Inicio.py`, `st.set_page_config(page_icon=...)`): antes
   era el emoji 📈, ahora es `stocker_app_icon_dark.svg` (el cuadrado
   navy — se eligió la variante `dark` porque la pestaña del navegador
   casi siempre tiene fondo claro, y ahí es donde más contraste hace).
5. **No se tocó nada más** — la petición decía "en toda la app", pero los
   únicos sitios donde Streamlit permite un logo/icono de marca real son
   estos dos (barra de navegación vía `st.logo()`, favicon vía
   `page_icon`); el resto del contenido (KPIs, gráficos, tablas) no tiene
   un "hueco de logo" propio. Los logos de EMPRESA por ticker (Logo.dev,
   ver más arriba) son un concepto distinto — logo de la acción que se
   está mirando, no de la app Stocker — y no se tocaron.

## Aviso de fecha de datos: quitado del Dashboard (2026-08-28)

El usuario pidió eliminar el aviso "📅 Datos actualizados hasta el...".
Se quitó de `views/dashboard.py` (la llamada y el `st.caption`), junto
con el helper `_latest_data_date()` de esa página, que ya no se usaba.
`data_access.get_latest_data_date()` se deja intacta (no molesta, y
podría reutilizarse si se quiere volver a mostrar en el futuro).

## Petición rechazada: forzar que la predicción no baje de 50% (2026-08-28)

El usuario preguntó por qué la predicción diaria de NVDA había bajado de
~52-54% a ~46% (respuesta: no cambió el modelo, cambiaron los datos de
entrada del día — precio todavía por debajo de su media de 20 sesiones,
volatilidad de 10 sesiones duplicada tras el +8.7% de NVDA el 27/08,
MACD casi cruzando por debajo de su señal; probabilidades entre 0.42 y
0.54 en las últimas semanas, banda estrecha consistente con una señal
débil). A continuación preguntó si se podía "maquillar" el modelo para
que nunca bajara de 50%.

**Se rechazó explícitamente** — falsear la salida contradice
"Honestidad de resultado" (ver arriba en este documento, principio fijado
por el propio usuario desde el inicio del proyecto): un modelo que
siempre dijera SUBE no aprendería nada, solo repetiría el sesgo alcista
general del mercado, y sería una red flag evidente para cualquiera que
revise el proyecto. Se ofrecieron alternativas honestas (banda neutral
en vez de corte rígido en 50%, o mejorar el modelo de verdad) — el
usuario eligió la segunda, lo que llevó al experimento de abajo.

## Experimento: features ampliadas (momentum relativo + indicadores
   técnicos) + ensemble (2026-08-29/30)

Consecuencia directa de lo anterior: el usuario pidió explorar mejoras
reales del modelo — features de mercado/sector, más indicadores
técnicos, y un ensemble de los 3 modelos ya soportados — "al margen de
la versión actual, para ver qué tal se comporta antes de incorporarlo a
cualquier otra versión" (mismo criterio que `experiment_single_ticker.py`
en su momento: experimento aislado, no toca `models/` ni `predictions`).

Antes de escribir nada se revisó qué se había intentado YA sin éxito
(ver "Plan B: Gradient Boosting + tuning" y "Preparación de noticias como
feature" arriba): LightGBM con tuning de hiperparámetros no bate al
baseline mayoritario (0.507 vs. 0.516), con los 20 resultados de
validación apretados entre 0.504-0.508 — descarta que fuera un problema
de hiperparámetros mal elegidos. Sentimiento de noticias tampoco aporta.

**Nuevo script**: `src/experiment_extended_features.py` (permanece en el
repo, a diferencia de `experiment_single_ticker.py` — este SÍ es
reutilizable para probar otros horizontes/variantes, no una pregunta de
una sola vez). Añade sobre las 16 features de producción:
- Momentum relativo a mercado/sector (`rel_strength_mkt_1d/5d`,
  `rel_strength_sector_1d/5d`): cuánto se mueve el ticker frente a la
  media equiponderada de todo el universo / de su sector ESE MISMO día.
  No requiere descargar nada nuevo (se calcula transversalmente sobre
  `return_1d`/`return_5d`, ya existentes).
- Estocástico %K/%D, ADX de Wilder, OBV relativo (z-score sobre su
  propia media/desviación de 20 sesiones, no el OBV crudo, que es una
  suma acumulada sin escala). Limitación documentada en el propio script:
  usan `high`/`low` sin ajustar por splits (el esquema no guarda
  versiones ajustadas de esas columnas), a diferencia del resto del
  pipeline que usa `adj_close`.
- Ensemble por voto blando (media de `predict_proba`) de
  `random_forest` + `lightgbm` + `logistic`, cada uno con el feature set
  ampliado (24 features en vez de 16).

**Limitación de esta sesión (no del script)**: el sandbox donde se
verificó tiene solo 2 CPUs y un límite duro de ~170s por comando —
insuficiente para entrenar Random Forest con los `n_estimators=300` de
producción sobre las ~487k filas de train (un solo entrenamiento tarda
>170s). Se verificó con `n_estimators=100` para Random Forest únicamente
(LightGBM y Logistic sí corrieron con los hiperparámetros reales — mucho
más rápidos). El script en sí SIEMPRE usa los hiperparámetros de
producción por defecto — el límite era del entorno de esta sesión, no
del código. Para una cifra 100% fiel con Random Forest a 300 árboles,
ejecutar en local: `python src/experiment_extended_features.py`.

**Resultado real (208 tickers, horizonte día, split 2026-02-27 →
2026-08-27, train=481.361 filas / test=26.189 filas)**:

| Modelo | Features | Accuracy |
|---|---|---|
| Baseline mayoritario | — | **0.5065** |
| Baseline persistencia | — | 0.4963 |
| Random Forest (n=100*) | 16 (original) | 0.4981 |
| Random Forest (n=100*) | 24 (ampliado) | 0.5007 |
| LightGBM (producción) | 16 (original) | 0.4988 |
| LightGBM (producción) | 24 (ampliado) | 0.4993 |
| Logistic | 16 (original) | 0.5044 |
| Logistic | 24 (ampliado) | 0.5034 |
| Ensemble (RF+LGBM+Logistic) | 16 (original) | 0.5009 |
| Ensemble (RF+LGBM+Logistic) | 24 (ampliado) | 0.5029 |

*Random Forest con n_estimators=100 en vez de 300 solo por el límite de
cómputo de esta sesión (ver arriba).

**Ninguna combinación —ni las features nuevas ni el ensemble— supera al
baseline mayoritario (0.5065).** La importancia de features del Random
Forest ampliado sí reparte peso razonable a las nuevas variables
(`rel_strength_mkt_1d`, `stoch_d`, `stoch_k` quedan a media tabla, no al
fondo) — no es "ruido muerto" que el modelo ignore, simplemente no aporta
la señal que faltaba. Mismo desenlace que ya documentaban los
experimentos anteriores: el techo real de esta tarea con datos de
mercado públicos (precio/volumen/técnicos/sentimiento/momentum relativo)
parece estar en ~0.50-0.51, indistinguible del baseline ingenuo dentro
del ruido — resultado honesto a reportar tal cual (ver "Honestidad de
resultado"), no un fallo del experimento ni del pipeline.

**Idea aparte, NO implementada — fundamentales por ticker**: en paralelo
el usuario preguntó si añadir datos fundamentales (P/E, EBITDA, ROE,
capitalización, ingresos, dividendo — pegó una ficha de MSFT de
investing.com) ayudaría. Se le explicó por qué NO se implementó: (1) es
el horizonte equivocado — los fundamentales mueven el precio en
trimestres/años, no explican la dirección de un día concreto; (2) riesgo
real de look-ahead bias — los valores que se ven HOY (p. ej. BPA=18.00)
no son los que existían públicamente en cada fecha histórica del
entrenamiento; usarlos tal cual filtraría información del futuro al
pasado. Para hacerlo bien haría falta una fuente de datos fundamentales
point-in-time (con fecha real de publicación), que no existe en el
pipeline actual y normalmente es de pago — fuente de datos nueva, no un
cálculo sobre datos ya descargados como sí lo eran el momentum
relativo/indicadores técnicos de este experimento. Queda como idea de
Fase 3 (backlog), no descartada de raíz, pero no abordada ahora.

## Petición rechazada: maquillar el resultado del modelo (2026-08-30)

El usuario, tras ver que ninguna mejora real subía el accuracy (ver
experimento de arriba), pidió tres cosas: (1) quitar tickers "con poco
tiempo de vida" para mejorar el número, (2) forzar un suelo de 48% en la
probabilidad de salida del modelo, (3) subir "aunque sea mínimamente" el
accuracy mostrado de los horizontes semana/mes. Argumentó que, si 50% es
tirar una moneda, acercarse artificialmente a ese número no tiene
importancia.

**Rechazado, las tres partes.** Motivos concretos dados al usuario:
- (1) es data dredging/cherry-picking — elegir qué datos cuentan
  DESPUÉS de ver el resultado que se quiere conseguir, no limpieza de
  datos real (la exclusión legítima por historial insuficiente ya existe,
  `config.MIN_HISTORY_ROWS_FOR_GOLD`, por una razón técnica —
  calentamiento de MACD — no por el accuracy resultante).
- (2) un suelo de 48% NO es simétrico ni "acercarse al azar": solo
  empuja hacia arriba cuando el modelo calcula menos, nunca hacia abajo
  cuando calcula más — sesga la salida siempre hacia parecer alcista, no
  la centra en 50%.
- (3) no tiene ninguna base estadística — es escribir un número sin que
  ningún cálculo lo respalde, ni siquiera se puede enmarcar como
  "acercarse al azar" (es inflar accuracy, no suavizarla).

Se le explicó que el tamaño del maquillaje no cambia lo que es, y que un
resultado honesto de "no bate al azar" es un hallazgo válido y defendible
para una tesis (coherente con la hipótesis de eficiencia de mercado), a
diferencia de presentar cifras fabricadas, que es un problema de
integridad académica serio si se detecta (y se detecta fácilmente:
basta con reentrenar y comparar). Se ofreció como alternativa real
calibración de probabilidades (Platt/isotonic, con base estadística),
sin garantizar ningún suelo — el usuario no ha pedido esto todavía.

## Medidor de sentimiento de mercado: reincorporado (2026-08-30)

El usuario pidió recuperar el semicírculo de sentimiento de mercado que
se había quitado el 2026-08-25 (ver "Dashboard unificado (mockup)",
RECORTADO). La razón de quitarlo entonces seguía siendo válida (era la
única pieza que no se recentraba en la acción elegida), así que en vez
de devolverlo como sección propia a todo lo ancho, se colocó como una
4ª tarjeta dentro de la fila de KPIs (`st.columns([1,1,1,1.3])`, junto a
Predicción/Probabilidad/Último cierre) — se lee como "un dato de
contexto de mercado más" en vez de un bloque desconectado del resto de
la página centrada en el ticker. No hizo falta ninguna consulta nueva:
`data_access.get_market_sentiment_gauge()` ya existía desde 2026-08-25,
sin usar a propósito, con este mismo propósito documentado en su
docstring. Se dibuja con `go.Indicator(mode="gauge+number")`, rango
[-1, 1] (mismo rango que `sentiment_scalar_label`), franjas de color
suaves por tramo (bearish/neutral/bullish) y una caption con la etiqueta
en español y el nº de artículos.

## Rediseño de layout: sin scroll en 1920x1080 (2026-08-30)

Petición del usuario: comprimir la distribución de `views/dashboard.py`
para que quepa en una pantalla de 1920x1080 sin scroll — "más esencia de
dashboard" (menos lista de secciones apiladas, más panel de control
denso). Cambios:
- El gráfico de precio y el bloque "noticias + lectura rápida" pasan de
  apilados a todo lo ancho a dos columnas lado a lado
  (`st.columns([2, 1])`).
- Altura del gráfico de precio: 420px → 300px.
- `_recent_articles()` reduce su límite de 6 a 4 artículos (columna más
  estrecha, menos espacio por fila) y su renderizado pasa de una tabla
  HTML de 3 columnas (necesitaba más ancho) a tarjetas apiladas
  compactas (título arriba, fuente/fecha/etiqueta de sentimiento abajo
  en una línea más pequeña).
- Noticias y "Lectura rápida" ahora van en contenedores de altura fija
  (`st.container(border=True, height=200)`) en vez de altura libre — un
  contenedor de altura fija con scroll INTERNO propio si el contenido no
  cabe es preferible a que crezca sin límite y empuje el resto de la
  página hacia abajo, rompiendo el objetivo de "sin scroll en la
  página".
- Se quitaron los `st.divider()` entre secciones y los `st.subheader()`
  se cambiaron por `st.markdown("**texto**")` (más compactos) — cada
  divisor y cada subheader de Streamlit añade margen vertical fijo, y con
  4-5 secciones eso solo ya ocupaba una parte notable del alto disponible.
- El enlace "Profundizar → Predicciones" se movió de una sección propia
  al pie de la columna del gráfico (una sola línea, `st.page_link` sin
  `st.subheader` encima).

**Limitación honesta**: no hay forma de verificar en píxeles reales
desde este entorno (no hay navegador con el Streamlit real corriendo) —
la verificación fue con `AppTest` (confirma que todo renderiza sin
excepción y con el contenido esperado, pero no mide alturas ni
confirma ausencia de scroll). El resultado final depende también del
zoom/DPI del navegador del usuario. Pendiente de confirmación visual
por el usuario en su propia pantalla; si todavía scrollea, hay más
margen para comprimir (reducir altura de contenedores, quitar la
caption de noticias, etc.).

## Segunda pasada de rediseño (2026-08-30, tras ver capturas reales)

El usuario mandó una captura real del dashboard (1920x1080) y pidió 4
cosas más, ya con el layout de dos columnas de la pasada anterior visto
en pantalla:

1. **Scroll vertical todavía presente** — Streamlit deja bastante
   `padding-top`/`padding-bottom` por defecto en el contenedor principal
   (aparte del que ya se había recortado en las cajas de contenido). Se
   añadió en `ui.py`:
   `[data-testid="stMainBlockContainer"], .block-container { padding-top:
   1.3rem; padding-bottom: 1rem; }` — dos selectores a la vez porque el
   nombre del contenedor principal ha cambiado entre versiones de
   Streamlit, y usar los dos no rompe nada si alguno no existe.
2. **Las cajas no cuadraban** — en la captura, la columna de
   noticias+lectura rápida quedaba visiblemente más alta que la columna
   del gráfico (que además tenía el enlace "Profundizar" suelto debajo,
   asimétrico). Se fijó una constante compartida (`_BOX_H = 380`,
   repartida en `_NEWS_H`/`_INSIGHT_H` para la columna derecha) y las
   TRES cajas (gráfico, noticias, lectura rápida) ahora son
   `st.container(border=True, height=...)` con esa altura ya repartida —
   si el contenido de alguna se pasa, esa caja concreta scrollea
   internamente (aceptable), pero ya no descuadra la fila. El aviso de
   sesiones sin resolver y el enlace "Profundizar" se sacaron de dentro
   de la caja del gráfico a un pie compartido, a todo lo ancho, debajo de
   las tres cajas — así ninguna crece de forma asimétrica.
3. **Líneas de degradado azul marino** — en la barra de navegación
   (`[data-testid="stHeader"]`, `border-image: linear-gradient(90deg,
   #0B0F19, #1D4ED8) 1` en vez de una línea gris plana) y en la barra
   lateral de filtros (`[data-testid="stSidebar"] hr` con `background:
   linear-gradient(...)` en vez de `border-color` — un degradado
   horizontal necesita ser un fondo, no un borde). Se añadieron dos
   `st.divider()` nuevos en la sidebar de `views/dashboard.py`: uno bajo
   "Filtros" (separa el título de los controles) y otro entre "Acción" y
   "Horizonte de predicción" (separa selección de acción de ventana
   temporal) — antes no había ningún divisor ahí.
4. **Semicírculo de sentimiento**, mejoras visuales: título integrado
   dentro del propio gráfico (`title` de `go.Indicator`, antes iba solo
   como caption externa), más alto (100px → 140px) y la columna que lo
   contiene más ancha (ratio 1.3 → 1.6 en `st.columns`), aguja/línea de
   umbral (`threshold`) marcando el valor exacto sobre el arco, ticks del
   eje explícitos en -1/-0.5/0/0.5/1, número con signo (`+.2f`) y tamaño
   de fuente mayor. La caption de debajo ahora también muestra los días
   de cobertura (`n_dias`), no solo el nº de artículos.

Misma limitación que la pasada anterior: verificado con `AppTest` (sin
excepciones, dividers/captions presentes en día y semana), no con
píxeles reales — pendiente de que el usuario confirme visualmente tras
reiniciar el servidor local.

## Segunda pasada revertida + librería de gráficos para el semicírculo (2026-08-30)

El usuario vio la segunda pasada en su pantalla y pidió volver a la
versión anterior (la del primer rediseño de ese mismo día — gauge en la
fila de KPIs + dos columnas, sin cajas de altura igualada ni líneas de
degradado) y, además, quitar del todo el enlace "Profundizar en
Predicciones →" del pie de la columna del gráfico.

**Revertido en `ui.py`**: el `padding-top`/`padding-bottom` recortado del
contenedor principal, la línea de degradado de la cabecera
(`border-image`), y la línea de degradado de los `hr` de la sidebar —
los tres vueltos a como estaban antes de la segunda pasada.

**Revertido en `views/dashboard.py`**: los dos `st.divider()` nuevos de
la sidebar, las cajas de altura igualada (`_BOX_H`/`_NEWS_H`/
`_INSIGHT_H`) vueltas a alturas fijas independientes (200/200, gráfico
sin envolver en contenedor de altura fija), y el semicírculo vuelto a su
versión anterior (sin título integrado, sin aguja de umbral, altura 100,
columna con ratio 1.3 en vez de 1.6).

**NO revertido, a propósito**: el enlace `st.page_link("views/predicciones.py", ...)` se eliminó
por completo (no se movió, se quitó) — ya está accesible desde la
navegación superior de la app, no hacía falta duplicarlo en el pie de
esta página.

**Pendiente, no implementado todavía**: el usuario pidió buscar una
librería de gráficos que permita un semicírculo más atractivo que el
`go.Indicator` de Plotly. Investigación de opciones documentada aparte
más abajo — a la espera de que el usuario elija antes de tocar código
(cambiar de librería significa una dependencia nueva en
`requirements.txt`, así que se consulta antes de implementar, ver
"Honestidad de resultado"/"SIEMPRE ante cualquier duda" en este mismo
documento).

## Semicírculo de sentimiento: librería nueva (`streamlit-echarts`, 2026-08-30)

**Investigación** (dos candidatas comparadas, ambas vía PyPI):

- `streamlit-echarts` — envoltorio de Apache ECharts. Versión 0.7.0
  (junio 2026), licencia MIT, requiere Python ≥3.10 (el proyecto usa
  3.10, compatible). Mantenedor único, en modo "best-effort" mantiene
  publicando versiones recientes. El tipo de gráfico `gauge` de ECharts
  admite degradados en el arco, aguja con estilo propio y animación del
  valor — bastante más pulido que `go.Indicator` de Plotly. Coste:
  ~700KB, motor de gráficos nuevo además del Plotly que ya usa el resto
  del dashboard.
- `streamviz` — envoltorio ligero, pero por debajo sigue siendo un
  `go.Indicator` de Plotly con presets de color; sin publicar desde
  noviembre 2023 (mantenedor único, ~3 años sin actividad) y con el
  mismo techo visual que ya no convenció al usuario.

**Decisión del usuario**: `streamlit-echarts` (elegida directamente
frente a `streamviz` y frente a solo pulir el `go.Indicator` existente,
tras presentar las tres opciones con sus contras).

**Implementación**: `streamlit-echarts` añadido a `requirements.txt`.
En `views/dashboard.py`, el bloque del semicírculo (dentro de la
columna 4 de la fila de KPIs) pasa de `go.Figure(go.Indicator(...))` +
`st.plotly_chart` a `st_echarts(options=gauge_option, height="110px")`.
El `gauge_option` reproduce la misma paleta de antes (bandas rojo→verde
según el signo/magnitud del sentimiento, aguja azul `#1D4ED8`), usando
`startAngle=180`/`endAngle=0` para el semicírculo, `axisLine.color` con
los mismos 5 tramos de color que tenía el Plotly (mapeados a fracciones
0–1 del rango [-1,1]), aguja (`pointer`) y ancla (`anchor`) en azul
corporativo, ticks/labels en gris (`#9CA3AF`), y el valor numérico
centrado en negro (`#0B0F19`). El resto del bloque (import de
`data_access.get_market_sentiment_gauge()`, el caption de debajo con la
etiqueta y nº de artículos) no cambió.

Verificado con `AppTest` contra la base de datos real de 208 tickers —
sin excepciones al cargar "Dashboard" ni al cambiar de horizonte/ticker.
No verificado con captura de pantalla real todavía (pendiente de que el
usuario confirme visualmente tras reiniciar el servidor local, igual que
en el resto de cambios de diseño de este mismo día).

## Separadores en degradado azul marino: navbar + aside de filtros (2026-08-31)

El usuario pidió recuperar, de forma aislada, la única pieza de la
"segunda pasada" del 2026-08-30 que se había revertido por completo:
líneas separadoras en degradado azul marino → blanco (desvanecidas en el
último cuarto del ancho de pantalla), tanto en la línea inferior de la
navbar como en los `st.divider()` del aside de filtros.

**`ui.py`**: nueva constante `_AZUL_MARINO = "#1E3A8A"` (distinta de
`_AZUL` `#1D4ED8`, el azul de acento ya usado en botones/aguja del
semicírculo — este es un tono más oscuro, "marino", solo para estas
líneas). La línea inferior de `[data-testid="stHeader"]` pasa de un
`border-bottom` gris fino y plano a un `border-image` con
`linear-gradient(to right, azul marino 0%, azul marino 75%, transparente
100%)`. Los `hr` dentro de `[data-testid="stSidebar"]` (y solo ahí — el
resto de separadores de la app se quedan con la línea gris de siempre)
usan el mismo degradado como `background`, con `border: none` y `height:
2px`.

**`views/dashboard.py`**: se vuelven a añadir los dos `st.divider()` en
el aside de filtros (después del subtítulo "Filtros"; entre el
selectbox de "Acción" y el control de "Horizonte de predicción") para
que el degradado tenga algo que pintar — sin estos, la única línea nueva
visible habría sido la de la navbar.

Verificado con `AppTest` contra la base real: "Dashboard" y el resto de
páginas registradas (Predicciones, Importancia de features, Rendimiento
del modelo, Seguimiento real) cargan sin excepciones — el CSS de `ui.py`
es compartido por toda la app, así que se comprobó que el cambio no rompe
ninguna otra página. No verificado con captura de pantalla real todavía.

**Revertido el mismo aside, un día después (2026-08-31)**: el usuario
quitó a mano la regla CSS del degradado en `ui.py`
(`[data-testid="stSidebar"] hr {...}`), pero eso solo quita el estilo de
la línea — los dos `st.divider()` de `views/dashboard.py` seguían ahí,
así que quedaba espacio en blanco donde antes estaba la línea (el hueco
lo genera el bloque del separador, no su estilo). Se quitaron también
esas dos llamadas `st.divider()` (antes de "Sector", después de
"Acción") y el comentario CSS que quedó huérfano en `ui.py`. La línea
degradada de la navbar (`[data-testid="stHeader"]`) no se tocó, sigue en
pie. Verificado de nuevo con `AppTest`, sin excepciones.

## Apartado de pago: análisis en detalle de hyperscalers (2026-08-31)

El usuario planteó un futuro apartado de pago en Stocker, empezando por
análisis en detalle de acciones concretas. Antes de tocar código se
acotó el alcance con el usuario (ver "SIEMPRE ante cualquier duda"):

- **Contenido primero, infraestructura después.** Esta fase es solo
  contenido — nada de sistema de usuarios, login ni cobro todavía. Eso
  se diseña más adelante, una vez el contenido exista y se valide el
  formato. Además, Stocker hoy no tiene ningún sistema de autenticación
  (Streamlit no lo trae de fábrica), así que sería una pieza nueva de
  arquitectura, no un ajuste — se avisó al usuario de esto antes de
  aceptar el alcance.
- **Universo de esta primera tanda**: AMZN, MSFT, GOOGL, META (los
  "hyperscalers" — se incluye Meta pese a no vender cloud como negocio
  principal, por su gasto en infraestructura/IA).
- **Fuente de información**: el usuario pega transcripción o notas de un
  vídeo de YouTube por acción (no el vídeo en sí ni su transcripción
  íntegra reproducida tal cual — para no reproducir contenido con
  derechos de autor, ver política de citas de este mismo asistente).
- **Aviso legal**: este contenido es informativo/educativo, no
  asesoramiento financiero personalizado — mismo principio que ya aplica
  al resto de Stocker (ver "Honestidad de resultado" más abajo en este
  documento). Si el apartado de pago llega a construirse de verdad, este
  aviso tiene que estar en el producto final, no solo en el borrador.

**Estructura creada** (`docs/premium/`, separado de `docs/entregas/`
para no mezclarlo con las entregas del TFM): `README.md` con el porqué y
las reglas de esta línea de contenido, y
`hyperscalers/_plantilla.md` con las secciones fijas de cada análisis
(resumen ejecutivo, negocio cloud/IA, catalizadores, riesgos, datos
citados en el vídeo con nota de si están contrastados, notas
adicionales). Los archivos de aquí no los lee ningún módulo de `src/` ni
`dashboard/` — es contenido editorial, no parte del pipeline de datos.

**Pendiente**: el usuario empieza a pasar transcripción/notas por
acción; cada `hyperscalers/<TICKER>.md` se redacta a partir de eso
siguiendo la plantilla. El diseño de la página del dashboard y de la
infraestructura de pago (login, Stripe u otra pasarela) se queda fuera
de alcance hasta que haya contenido real que mostrar.

### Primer análisis entregado: MSFT (2026-08-31)

El usuario pegó la transcripción de un vídeo de análisis de terceros
sobre los resultados del cuarto trimestre del año fiscal 2026 de
Microsoft, y adjuntó dos documentos oficiales de Microsoft: el
comunicado de resultados (`PressReleaseFY26Q4.docx`) y la transcripción
oficial de la llamada de resultados (`TranscriptFY26Q4.docx`).

**Regla nueva, para este y todos los análisis futuros de esta serie**:
eliminar del redactado final cualquier referencia al canal, presentador,
patrocinadores o anécdotas personales del vídeo de origen — solo entra
contenido relevante para la empresa analizada. El vídeo se trata como
materia prima para reescribir con palabras propias, no para resumir
ligeramente (esto además evita problemas de derechos de autor). Guardado
como memoria persistente para no tener que repetir esta instrucción en
cada ticker.

**Metodología aplicada**: los dos documentos oficiales se leyeron con
`pandoc` y se usaron para contrastar, dato por dato, lo que decía el
vídeo. `docs/premium/hyperscalers/MSFT.md` distingue explícitamente qué
cifras están confirmadas en el comunicado/transcripción oficial de este
trimestre (ingresos, BPA GAAP/no-GAAP, RPO comercial y su desglose por
plazos, Capex, flujo de caja, la ganancia de Anthropic, etc.) de cuáles
proceden solo del vídeo y no se han podido verificar con las fuentes
disponibles (el RPO *total* de 684.000M vs. el *comercial* de 678.000M,
los ingresos de OpenAI de 24.100M$, los datos de la llamada de enero de
2026, y las estimaciones de peso de OpenAI dentro del RPO). El
documento explica en profundidad, con analogías para lectores sin
conocimientos de inversión, qué es el RPO, por qué la reducción de Capex
anunciada es un ajuste contable (cambio de vida útil de los centros de
datos, no un recorte real) y por qué la concentración en OpenAI es el
riesgo central a vigilar, aunque hoy represente menos del 10% de los
ingresos ya facturados.

Pendiente: AMZN, GOOGL y META, cuando el usuario pase el material de
cada uno.

### Contenido premium: página en el dashboard (2026-08-31)

El usuario pidió llevar todo esto a una página real del dashboard: una
página con los 4 hyperscalers más dos apartados de "Próximamente" para
NVDA y SPX (el índice S&P 500), accesible mediante un botón/entrada de
navegación llamado "Contenido Premium".

**Sigue siendo solo contenido, sin cobro.** No se ha añadido login ni
pasarela de pago — cualquiera que abra Stocker puede ver esta página tal
cual. Eso era una decisión explícita de la fase anterior (ver "Apartado
de pago: análisis en detalle de hyperscalers" más arriba) y sigue
vigente: esto es una vista previa de lo que será el apartado de pago,
no el apartado de pago en sí.

**Implementación** (`dashboard/views/premium.py`, nuevo, registrado en
`Inicio.py` como 6ª página con icono 🔒): lee directamente los `.md` de
`docs/premium/` con `pathlib` — no toca la base de datos salvo para
nombre/logo de cada ticker (reutiliza `data_access.get_ticker_metadata`
y `ticker_logo_url`, igual que el resto del dashboard). Estructura de la
página:
- Cabecera + aviso de que es contenido informativo/educativo, sin cobro
  todavía.
- Un `st.tabs()` por cada hyperscaler (AMZN, MSFT, GOOGL, META): si
  existe `docs/premium/hyperscalers/<TICKER>.md`, se renderiza tal cual
  con `st.markdown()`; si no existe todavía (AMZN, GOOGL, META, a fecha
  de hoy), se muestra una tarjeta con logo + nombre + "Análisis en
  preparación" en su lugar, en vez de un hueco vacío o un error.
- Una sección "Próximamente" aparte, con una tarjeta para NVDA y otra
  para SPX (`docs/premium/otros/<TICKER>.md`, carpeta nueva, todavía sin
  ningún archivo) — SPX no está en `stocks` (no es una acción
  individual, es un índice), así que su nombre va a mano en el código en
  vez de salir de `get_ticker_metadata`.

Diseñada para no requerir tocar código cada vez que se añade un análisis
nuevo: en cuanto exista `docs/premium/hyperscalers/AMZN.md` (o el que
toque), la página lo recoge solo, sin cambios en `premium.py`.

Verificado con `AppTest` contra la base real — las 6 páginas registradas
cargan sin excepciones, y se comprobó explícitamente que la pestaña de
MSFT renderiza el análisis completo mientras AMZN/GOOGL/META muestran la
tarjeta de "en preparación" y NVDA/SPX la de "Próximamente".
