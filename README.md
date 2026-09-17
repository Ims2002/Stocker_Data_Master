# Stocker

Sistema de predicción diaria de dirección de precio (sube/baja al día
siguiente, clasificación binaria — no predice el precio exacto) para un
universo de 208 acciones de EE. UU., a partir del histórico diario de
Yahoo Finance, con un modelo agrupado (pooled) y un dashboard interactivo
para explorar predicciones, importancia de features y rendimiento real
frente a baselines.

Proyecto académico de un máster de Data Science, orientado a producción
real. El diseño completo — esquema de datos, variable objetivo, contrato
de evaluación, decisiones de modelado y el histórico de incidentes reales
encontrados y corregidos — está documentado en
[`CONTEXTO.md`](CONTEXTO.md); este README es la puerta de entrada rápida.

## Qué hace

- Descarga histórico diario OHLCV de 208 tickers vía `yfinance`, y
  metadatos de empresa (nombre/sector/país) vía `enrich_stocks.py`.
- Descarga noticias con sentimiento ya puntuado (Alpha Vantage
  `NEWS_SENTIMENT`) para el mismo universo, con backfill de ~2 años
  automatizable a diario (ver `run_news_daily.bat`) — todavía sin usar
  como feature del modelo, solo poblando la base de datos.
- Construye un histórico limpio (`daily_prices`) y una capa de
  features/target sin fugas de información del futuro al pasado
  (`gold_train`/`gold_inference`, físicamente separadas).
- Entrena un clasificador binario agrupado (Random Forest por defecto) y
  lo evalúa frente a los baselines obligatorios (clase mayoritaria y
  persistencia) con split temporal, nunca aleatorio — reporta el
  resultado tal cual, gane o no el modelo (ver "Honestidad de resultado"
  en CONTEXTO.md).
- Genera y persiste una predicción por ticker para la siguiente sesión de
  mercado real, a tres horizontes (día/semana/mes, ver "Horizontes de
  predicción" en CONTEXTO.md).
- Dashboard interactivo (Streamlit) para explorar todo lo anterior — ver
  más abajo.

Queda fuera de esta fase: usar el sentimiento de noticias como feature
del modelo (investigado, sin señal real encontrada — ver CONTEXTO.md),
el mercado español (aparcado por falta de calendario de mercado BME en
las librerías usadas), y actualización automática de la versión
publicada (ver "Demo publicada" más abajo — es una foto fija, no un
producto vivo).

## Instalación

```bash
git clone <url-del-repo>
cd stocker_project
python -m venv venv
source venv/bin/activate     # en Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env         # y rellena ALPHA_VANTAGE_API_KEY (solo necesaria para news.py)
```

## Ejecutar el pipeline

> **Revisión de auditoría (16/09/2026).** Esta copia incluye las
> correcciones de la auditoría: base de datos reparable, sin precios de
> media sesión, splits y dividendos, predicciones de los tres horizontes y
> más. Qué cambió y qué hay que ejecutar una vez está en
> [`CAMBIOS_AUDITORIA.md`](CAMBIOS_AUDITORIA.md).

Carga inicial (una vez):

```bash
python src/download.py          # histórico OHLCV de los tickers -> data/raw/
python src/enrich_stocks.py     # nombre/sector/país -> stocks
python src/load.py              # data/raw/ -> daily_prices
python src/gold.py              # daily_prices -> gold_train / gold_inference
python src/model.py --horizon 1 # entrena y evalúa (repetir con --horizon 5 y 20)
```

Uso diario — un único comando, **después del cierre de EE. UU. (22:30 en
Madrid)**:

```bash
python src/run_pipeline.py      # descarga, carga, gold, predicciones (1/5/20), resolución y noticias
```

Mantenimiento:

```bash
python src/maintenance.py check     # integridad de stocker.db
python src/maintenance.py repair    # copia de seguridad + reconstrucción de gold_*
python src/load.py --refresh APH    # re-descarga el histórico completo de un ticker
python src/backtest_walkforward.py  # evaluación año a año frente a los baselines
python -m pytest tests -q           # pruebas de las correcciones
```

Cada script trata los tickers de forma aislada: un fallo en uno no aborta el
resto del lote (ver `src/README.md` para el detalle de cada módulo).

### Noticias y sentimiento en piloto automático

`python src/news.py` (sin flags) decide solo si le toca seguir con el
backfill histórico o pasar al mantenimiento diario — pensado para
programarse una vez con el Task Scheduler de Windows y olvidarse. Ver
`run_news_daily.bat` y la sección "Automatizar `news.py`" en
[`src/README.md`](src/README.md) para el comando exacto y cómo comprobar
que ha corrido.

## El dashboard

```bash
python -m streamlit run dashboard/Inicio.py
```

(`streamlit run ...` a secas puede fallar en Git Bash en Windows si el
directorio de scripts de pip no está en el PATH — `python -m streamlit
run ...` lo evita siempre.)

Cinco páginas registradas en el v1 (ver "Roadmap v1" más abajo), todas de
solo lectura sobre `data/stocker.db` y `models/` (el dashboard nunca
escribe nada — eso lo hacen los scripts de `src/`):

- **Dashboard** (entrada) — vista única recentrada en la acción que
  elijas: predicción para el horizonte elegido (día/semana/mes),
  histórico con aciertos/fallos del backtest, últimas noticias de esa
  acción y un resumen en texto generado a partir de los mismos datos.
- **Predicciones** — selector de ticker, sector y horizonte, gráfico de
  histórico real con el backtest del modelo superpuesto (marcando
  claramente dónde empieza el test real nunca visto por el modelo) y la
  predicción para la próxima sesión.
- **En qué se fija** (importancia de features) — qué variables pesan más
  en el modelo (una sola importancia global, el modelo no usa el ticker
  como feature).
- **¿Funciona de verdad?** (rendimiento del modelo) — modelo vs.
  baselines obligatorios sobre el test temporal real, con el mismo
  criterio de honestidad de resultado que el resto del proyecto.
- **Día a día** (seguimiento real) — acierto real en producción
  (`predictions.actual_target_up_down`), no backtest, por versión de
  modelo.

Si el modelo cargado se entrenó antes de que `model.py` empezara a
guardar las métricas junto al `.joblib`, la página de rendimiento lo
avisa y pide reentrenar — vuelve a ejecutar `python src/model.py` una vez
si ves ese aviso.

"Resumen", "Noticias de la acción" y "Sentimiento del mercado" existen
como archivos en `dashboard/views/` pero no están registrados en el v1
(ver `dashboard/Inicio.py` y CONTEXTO.md, "Roadmap v1 (MVP para
publicar)") — se recuperan añadiendo una línea si hace falta.

### Demo publicada (datos congelados)

La versión desplegada públicamente NO es un producto vivo: es una foto
fija de la base de datos y los modelos en el momento de publicar, sin
pipeline corriendo en la nube. El propio Dashboard avisa de hasta qué
fecha llegan los datos ("Datos actualizados hasta...") — si esa fecha no
avanza, es la demo, no la versión en vivo.

Para reproducir esa demo en local (universo reducido a los tickers más
relevantes en vez de los 208 completos, ~60MB en vez de ~254MB):

```bash
python src/export_demo_db.py                    # genera data/stocker_demo.db
# copia a mano los .joblib de los 3 horizontes elegidos a models_demo/
STOCKER_DB_PATH=data/stocker_demo.db STOCKER_MODELS_DIR=models_demo \
    python -m streamlit run dashboard/Inicio.py
```

Sin esas dos variables de entorno, todo se comporta exactamente igual
que siempre (`data/stocker.db` / `models/`, los 208 tickers).

## Estructura del repositorio

```
stocker_project/
├── data/
│   ├── raw/            # CSV/JSON tal cual descargado (yfinance/Alpha Vantage), auditoría
│   ├── processed/       # daily_prices: histórico limpio y tipado, + log de calidad
│   └── gold/             # (referencia; las tablas gold viven en stocker.db, no en ficheros)
├── models/               # modelos entrenados versionados (*.joblib), gitignored
├── models_demo/          # 3 modelos elegidos para la demo publicada — SÍ versionado, ver "Demo publicada"
├── src/                  # pipeline: descarga, limpieza, features, modelo, noticias (ver src/README.md)
├── dashboard/            # app Streamlit de solo lectura (ver dashboard/README.md)
├── notebooks/
│   └── exploratory/      # notebooks exploratorios, no de producción
├── docs/
│   ├── entregas/          # entregas del curso (histórico de las decisiones de diseño)
│   └── ROADMAP_MVP.md     # checklist accionable hacia la primera publicación
├── run_news_daily.bat     # tarea programable para src/news.py (ver src/README.md)
├── CONTEXTO.md            # diseño de referencia y bitácora de decisiones/incidentes reales
├── requirements.txt
└── README.md
```

`data/` y `models/` están vacíos en el repo (solo se versiona la
estructura de carpetas): se regeneran desde la fuente ejecutando el
pipeline — el repo lleva el código que produce los datos, no los datos en
sí. Única excepción: `data/stocker_demo.db` y `models_demo/` (ver "Demo
publicada" arriba) SÍ se versionan — son la foto fija reducida que usa
la versión publicada, no la base de datos real.

## Las capas de datos

Según el diseño cerrado en `CONTEXTO.md`:

**Raw** (`data/raw/`) — CSV/JSON tal como lo devuelve la fuente (yfinance
para precios, Alpha Vantage para noticias), sin transformar. Copia de
auditoría de qué se descargó y cuándo.

**Processed** (tabla `daily_prices`) — histórico limpio: tipos correctos,
fechas ISO sin componente horaria, duplicados resueltos por upsert en
`(ticker, date)`, sin huecos que no correspondan a días no bursátiles, sin
registros inválidos.

**Gold** — tres artefactos con responsabilidades separadas a propósito,
para que el data leakage sea estructuralmente imposible:

- `gold_train`: histórico con features y target (`target_up_down`) ya
  calculados, de todos los tickers; excluye por construcción la última
  fecha disponible de cada uno.
- `gold_inference`: una fila por ticker (el día más reciente), solo con
  features, sin ninguna columna de label ni siquiera vacía.
- `predictions`: registro persistente y obligatorio de cada predicción
  real emitida, con la versión del modelo.

**Noticias** (`news_articles` + vista `news_sentiment_daily`) — sentimiento
por artículo y ticker (Alpha Vantage), agregado a diario mediante una
vista SQL (nunca una tabla física, para que no pueda desincronizarse).
Todavía no conectado como feature del modelo.

## Próximos pasos

Checklist accionable y priorizado hacia la primera publicación en
[`docs/ROADMAP_MVP.md`](docs/ROADMAP_MVP.md); el porqué de cada decisión
en `CONTEXTO.md`, "Roadmap v1 (MVP para publicar)". A grandes rasgos, lo
que falta ahora mismo es desplegar la demo (crear cuenta en el hosting
elegido, conectar el repo, publicar) — el código y los datos reducidos ya
están listos.

Fuera del v1, backlog explícito (no descartado, solo pospuesto):

- Producto vivo con pipeline corriendo en la nube (la demo v1 es una foto
  fija, no se actualiza sola).
- Reincorporar "Resumen", "Noticias de la acción" y "Sentimiento del
  mercado" a la navegación.
- Universo completo de 208 tickers en la versión pública.
- Sentimiento de noticias como feature real del modelo (investigado, sin
  señal encontrada — ver CONTEXTO.md).
- Limpieza pendiente de baja prioridad: filas huérfanas en `stocks`
  (tickers ya corregidos), tablas legacy `gold_aapl_train`/
  `gold_aapl_inference` sin eliminar físicamente.

Ver [`src/README.md`](src/README.md) para el detalle de cada módulo.
