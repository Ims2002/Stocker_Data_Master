# Stocker

Sistema propio de histórico y predicción diaria del precio de una acción.
En el MVP, para **AAPL**: no predice el precio exacto de cierre, sino si
subirá o bajará al día siguiente (clasificación binaria), a partir del
histórico diario de Yahoo Finance.

Proyecto académico de un máster de Data Science. El diseño completo —
esquema de datos, variable objetivo, contrato de evaluación y reglas de
limpieza — está documentado en [`CONTEXTO.md`](CONTEXTO.md); este README es
la puerta de entrada rápida para quien abra el repo.

## Qué hace (y qué no, todavía)

- Descarga histórico diario de AAPL (Open, High, Low, Close, Adj Close,
  Volume) vía la API de Yahoo Finance, a través de la librería `yfinance`.
- Construye un histórico limpio y una capa de features/target lista para
  entrenar un modelo, sin fugas de información del futuro al pasado.
- Entrena un clasificador binario (sube/baja al día siguiente) y lo evalúa
  frente a baselines simples con un split temporal, nunca aleatorio.
- Queda fuera del MVP: varias acciones a la vez y fuentes externas
  (noticias, datos macro, sentimiento).

**Estado actual**: solo está montado el andamiaje del repo (estructura de
carpetas, configuración, documentación). El pipeline de descarga, limpieza,
features y modelo todavía no está implementado — ver "Próximos pasos".

## Instalación

```bash
git clone <url-del-repo>
cd stocker_project
python -m venv venv
source venv/bin/activate     # en Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Estructura del repositorio

```
stocker_project/
├── data/
│   ├── raw/          # CSV tal cual descargado de yfinance, sin transformar
│   ├── processed/    # tabla daily_prices: histórico limpio y tipado
│   └── gold/          # gold_aapl_train, gold_aapl_inference, predictions
├── src/               # pipeline: descarga, limpieza, features, modelo (ver src/README.md)
├── notebooks/
│   └── exploratory/   # notebooks exploratorios, no de producción
├── docs/
│   └── entregas/       # entregas del curso (histórico de las decisiones de diseño)
├── CONTEXTO.md         # diseño de referencia: esquema, target, contrato de evaluación, reglas de limpieza
├── requirements.txt
└── README.md
```

`data/` está vacío en el repo (solo se versiona la estructura de carpetas)
porque se regenera desde la fuente: el repo lleva el código que produce los
datos, no los datos en sí.

## Las tres capas de datos

Según el diseño cerrado en `CONTEXTO.md`:

**Raw** (`data/raw/`) — CSV tal como lo devuelve `yfinance`, uno por
descarga, sin ninguna transformación. Actúa como copia de auditoría de qué
se descargó y cuándo.

**Processed** (`data/processed/`, tabla `daily_prices`) — histórico limpio:
tipos correctos, fechas ISO sin componente horaria, duplicados resueltos
por upsert en `(ticker, date)`, sin huecos que no correspondan a días no
bursátiles (los huecos de calendario se distinguen de fallos reales de
descarga con `pandas_market_calendars`), sin registros inválidos (Open/Close
nulos o negativos, Volume negativo).

**Gold** (`data/gold/`) — no es una única tabla, sino tres artefactos con
responsabilidades separadas, a propósito, para que el data leakage sea
estructuralmente imposible:

- `gold_aapl_train`: histórico con features y target (`target_up_down`) ya
  calculados; excluye por construcción la última fecha disponible, cuyo
  `close_next_day` todavía no existe. Solo para entrenar/evaluar.
- `gold_aapl_inference`: una única fila (el día más reciente disponible),
  solo con las features, sin ninguna columna de label ni siquiera vacía. Es
  la entrada directa para predecir "mañana".
- `predictions`: registro persistente y **obligatorio** de cada predicción
  emitida en producción, con la versión del modelo que la generó, para
  poder comparar el rendimiento real acumulado frente al backtest.

## Próximos pasos

Todavía no implementado (siguiente fase del proyecto):

- Descarga y persistencia en `data/raw/`.
- Carga y limpieza en `daily_prices`.
- Cálculo de features y construcción de `gold_aapl_train` / `gold_aapl_inference`.
- Entrenamiento y evaluación del modelo frente a los baselines obligatorios (clase mayoritaria y persistencia del día anterior), con split temporal.
- Persistencia de cada predicción real en `predictions`.

Ver [`src/README.md`](src/README.md) para el detalle de qué módulo cubrirá
cada paso.
