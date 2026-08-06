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
  mercado real.
- Dashboard interactivo (Streamlit) para explorar todo lo anterior — ver
  más abajo.

Queda fuera de esta fase: horizontes de predicción a semana/mes (la UI ya
tiene el selector preparado, pero solo día está entrenado), usar el
sentimiento de noticias como feature del modelo, y el mercado español
(aparcado por falta de calendario de mercado BME en las librerías
usadas).

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

```bash
python src/download.py          # histórico OHLCV de los 208 tickers -> data/raw/
python src/enrich_stocks.py     # nombre/sector/país -> stocks
python src/load.py              # data/raw/ -> daily_prices
python src/gold.py              # daily_prices -> gold_train / gold_inference
python src/model.py             # entrena y evalúa -> models/*.joblib
python src/predict.py           # gold_inference -> predictions
python src/news.py              # noticias/sentimiento (modo automático, ver más abajo)
```

Cada script trata los tickers de forma aislada: un fallo en uno no aborta
el resto del lote (ver `src/README.md` para el detalle de cada módulo).

### Noticias y sentimiento en piloto automático

`python src/news.py` (sin flags) decide solo si le toca seguir con el
backfill histórico o pasar al mantenimiento diario — pensado para
programarse una vez con el Task Scheduler de Windows y olvidarse. Ver
`run_news_daily.bat` y la sección "Automatizar `news.py`" en
[`src/README.md`](src/README.md) para el comando exacto y cómo comprobar
que ha corrido.

## El dashboard

```bash
streamlit run dashboard/Inicio.py
```

Tres páginas, todas de solo lectura sobre `data/stocker.db` (el dashboard
nunca escribe nada — eso lo hacen los scripts de `src/`):

- **Predicciones** — selector de ticker y de horizonte (solo "día" está
  activo; semana/mes están en la interfaz pero deshabilitados, ver
  CONTEXTO.md), gráfico de histórico real con el backtest del modelo
  superpuesto (aciertos/fallos, marcando claramente dónde empieza el
  test real nunca visto por el modelo) y la predicción para la próxima
  sesión.
- **Importancia de features** — qué variables pesan más en el modelo
  (una sola importancia global, el modelo no usa el ticker como
  feature).
- **Rendimiento del modelo** — modelo vs. baselines obligatorios sobre
  el test temporal real, con el mismo criterio de honestidad de
  resultado que el resto del proyecto.

Si el modelo cargado se entrenó antes de que `model.py` empezara a
guardar las métricas junto al `.joblib`, la página de rendimiento lo
avisa y pide reentrenar — vuelve a ejecutar `python src/model.py` una vez
si ves ese aviso.

## Estructura del repositorio

```
stocker_project/
├── data/
│   ├── raw/            # CSV/JSON tal cual descargado (yfinance/Alpha Vantage), auditoría
│   ├── processed/       # daily_prices: histórico limpio y tipado, + log de calidad
│   └── gold/             # (referencia; las tablas gold viven en stocker.db, no en ficheros)
├── models/               # modelos entrenados versionados (*.joblib), gitignored
├── src/                  # pipeline: descarga, limpieza, features, modelo, noticias (ver src/README.md)
├── dashboard/            # app Streamlit de solo lectura (ver dashboard/README.md)
├── notebooks/
│   └── exploratory/      # notebooks exploratorios, no de producción
├── docs/
│   └── entregas/          # entregas del curso (histórico de las decisiones de diseño)
├── run_news_daily.bat     # tarea programable para src/news.py (ver src/README.md)
├── CONTEXTO.md            # diseño de referencia y bitácora de decisiones/incidentes reales
├── requirements.txt
└── README.md
```

`data/` y `models/` están vacíos en el repo (solo se versiona la
estructura de carpetas): se regeneran desde la fuente ejecutando el
pipeline — el repo lleva el código que produce los datos, no los datos en
sí.

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

Ver la lista completa y priorizada en `CONTEXTO.md`, pero a grandes
rasgos:

- Terminar el backfill de noticias (~2 años, se completa solo vía
  `run_news_daily.bat`) y decidir cómo incorporar el sentimiento al
  modelo.
- Módulo que rellene `predictions.actual_target_up_down` con el cierre
  real, para medir precisión real en producción (no solo backtest).
- Entrenar modelos a horizonte semana/mes si se decide ampliar el
  selector ya preparado en el dashboard.
- Limpieza pendiente de baja prioridad: filas huérfanas en `stocks`
  (tickers ya corregidos), tablas legacy `gold_aapl_train`/
  `gold_aapl_inference` sin eliminar físicamente.

Ver [`src/README.md`](src/README.md) para el detalle de cada módulo.
