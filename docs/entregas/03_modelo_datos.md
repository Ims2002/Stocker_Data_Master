# Entrega 3 — Diseño del modelo de datos y capa gold del proyecto

> **Nota sobre esta revisión**: esta versión incorpora el feedback recibido, centrado en tres puntos: (1) separar con rigor el dataset de entrenamiento del de inferencia, de modo que `close_next_day` y `target_up_down` sean siempre etiquetas y nunca variables de entrada; (2) tratar la persistencia de predicciones y versión de modelo como parte obligatoria del diseño, no como algo opcional; (3) cerrar explícitamente el contrato de evaluación (validación temporal honesta frente a baselines simples, sin leakage).

## 1. Resumen de la idea y datos del proyecto

**Stocker** aborda la falta de una herramienta propia que permita a un inversor particular hacer seguimiento del histórico de AAPL y disponer de una señal orientativa diaria sobre si el precio subirá o bajará al día siguiente. La solución construye un histórico diario de precios y entrena un modelo de **clasificación binaria**, cuya variable objetivo se obtiene desplazando el `Close` un día hacia atrás y comparándolo con el `Close` actual. La única fuente de datos es la API de Yahoo Finance (vía `yfinance`), que aporta el histórico diario de cotización (Open, High, Low, Close, Adj Close, Volume) necesario para construir tanto las features como el target.

## 2. Tecnología o formato de almacenamiento elegido

Se mantiene la combinación decidida en la entrega anterior: **CSV como capa de intercambio inicial (raw)** — volcado tal cual de `yfinance`, sin transformar, que actúa como copia de auditoría — y **SQLite como base de datos para processed y gold**, suficiente para el volumen de una sola acción, sin servidor que mantener, con migración natural a PostgreSQL en el VPS de Hetzner si el proyecto escala a más tickers.

## 3. Estructura de capas de datos

```
data/
├── raw/
│   └── aapl_YYYYMMDD.csv            # volcado tal cual de la API
├── processed/
│   └── daily_prices (tabla SQL)     # histórico limpio y tipado
└── gold/
    ├── gold_aapl_train (tabla/vista)      # histórico con target conocido — SOLO para entrenar/evaluar
    ├── gold_aapl_inference (tabla/vista)  # última fila disponible — SOLO features, sin columnas de target
    └── predictions (tabla SQL)            # histórico de predicciones emitidas, obligatorio
```

| Capa | Contenido esperado |
|---|---|
| **Raw** | CSV con los datos tal como los devuelve `yfinance`, sin ninguna transformación. |
| **Processed** | Tabla `daily_prices`: tipos correctos, fechas ISO, sin duplicados (upsert por `ticker, date`), sin huecos que no correspondan a días no bursátiles. |
| **Gold** | Ya no es una única tabla, sino **tres artefactos con responsabilidades separadas** (ver sección 4), precisamente para que sea estructuralmente imposible que una etiqueta se cuele como feature. |

## 4. Definición de la capa gold

La capa gold se divide en **tres datasets con propósitos distintos**, en vez de una única tabla, como corrección directa al feedback recibido: mezclar en una sola tabla las features y el target — con el target a `NULL` en la última fila — deja la separación en manos de la disciplina de quien escriba el código de entrenamiento. Separar físicamente entrenamiento e inferencia lo hace imposible por diseño.

### 4.1 `gold_aapl_train` — dataset de entrenamiento/evaluación

| Aspecto | Detalle |
|---|---|
| Descripción funcional | Histórico completo de AAPL con features y target ya calculados, **únicamente para las filas donde el target es conocido** (es decir, se excluye por construcción la última fecha disponible, cuyo `close_next_day` todavía no existe). |
| Granularidad | Una fila por día de cotización con target conocido. |
| Registros esperados | ~1.250 (5 años) a ~2.500 (10 años), menos 1 fila respecto al histórico total. |
| Campos | `ticker`, `date` (PK), **features**: `open`, `high`, `low`, `close`, `adj_close`, `volume`, `return_1d`, `ma_5`, `ma_10`, `ma_20`, `volatility_10d`; **labels**: `close_next_day`, `target_up_down`. |
| Uso posterior | Entrenamiento del modelo y validación temporal frente a baselines (ver sección 8). |

### 4.2 `gold_aapl_inference` — dataset de inferencia

| Aspecto | Detalle |
|---|---|
| Descripción funcional | **Una única fila**: la del día más reciente disponible, con únicamente las columnas de features. No contiene `close_next_day` ni `target_up_down` porque para esa fecha son, por definición, desconocidas — no se materializan ni siquiera como columnas vacías, para que no puedan pasarse por error al modelo. |
| Granularidad | Una fila (la del día actual). |
| Registros esperados | 1. |
| Campos | `ticker`, `date` (PK), y exactamente las mismas features que en `gold_aapl_train`, en el mismo orden — ninguna columna de label. |
| Uso posterior | Entrada directa al modelo ya entrenado para generar la predicción de mañana. |

### 4.3 `predictions` — histórico de predicciones (obligatorio, no opcional)

| Aspecto | Detalle |
|---|---|
| Descripción funcional | Registro persistente de cada predicción emitida, con la versión del modelo que la generó, para poder medir el rendimiento real del sistema con el tiempo — no solo el rendimiento de backtest. |
| Granularidad | Una fila por predicción emitida (una por día, en el MVP de una sola acción). |
| Campos | `ticker`, `date_predicha` (la fecha sobre la que se predice, es decir, "mañana"), `predicted_target_up_down`, `predicted_probability`, `model_version`, `predicted_at` (timestamp de generación), `actual_target_up_down` (se rellena un día después, cuando el dato real ya existe). |
| Clave primaria | `(ticker, date_predicha, model_version)`. |
| Uso posterior | Cálculo de accuracy/precision real acumulada del modelo en producción, y comparación frente al backtest — si diverge de forma relevante, es señal de alerta sobre el propio pipeline. |

## 5. Relaciones entre datos

```
stocks.ticker                    1 --- N   daily_prices.ticker
daily_prices.(ticker,date)       1 --- 1   gold_aapl_train.(ticker,date)   (excluye la última fecha disponible)
daily_prices.(ticker,date)       1 --- 1   gold_aapl_inference.(ticker,date) (solo la última fecha disponible)
gold_aapl_inference.(ticker,date) 1 --- N  predictions.(ticker,date_predicha) (N si se versionan varios modelos)
predictions.(ticker,date_predicha) 1 --- 1 daily_prices.(ticker,date)  (join posterior para rellenar actual_target_up_down una vez conocido el cierre real)
```

`gold_aapl_train` y `gold_aapl_inference` son transformaciones fila a fila de `daily_prices`, mutuamente excluyentes por fecha (la fecha más reciente va a inference, el resto a train). No hay joins N:M al trabajar con un único ticker; el problema a vigilar si se amplía a más acciones es el mismo señalado en la entrega anterior: verificar que los calendarios de cotización coincidan entre tickers.

## 6. Diccionario de datos inicial

| Campo | Descripción | Tipo | Rol | Obligatorio | Observaciones |
|---|---|---|---|---|---|
| `ticker` | Símbolo bursátil | string | clave | Sí | Fijo "AAPL" en el MVP |
| `date` | Fecha de la sesión | date | clave | Sí | ISO, solo días bursátiles |
| `open`, `close`, `adj_close` | Precios de apertura/cierre/cierre ajustado | float | **feature** | Sí | Retornos y medias calculados sobre `adj_close` |
| `high`, `low` | Máximo/mínimo de sesión | float | **feature** | Deseable | No usados como feature en el modelo inicial |
| `volume` | Volumen negociado | integer | **feature** | Sí | Revisar si aparece en 0 |
| `return_1d`, `ma_5/10/20`, `volatility_10d` | Variables derivadas | float | **feature** | Sí/Deseable | Calculadas solo con datos ≤ día actual (sin lookahead) |
| `close_next_day` | Close del día siguiente | float | **label** | Sí (solo en train) | Nunca presente en `gold_aapl_inference` |
| `target_up_down` | Sube/baja el día siguiente | integer (0/1) | **label** | Sí (solo en train) | Nunca presente en `gold_aapl_inference` |
| `predicted_target_up_down`, `predicted_probability` | Salida del modelo | int/float | salida | Sí | En `predictions` |
| `model_version` | Identificador de la versión del modelo | string | metadato | Sí | En `predictions`, imprescindible para trazabilidad |
| `actual_target_up_down` | Resultado real, una vez conocido | integer (0/1) | verificación | Se rellena a posteriori | En `predictions`, permite medir rendimiento real |

## 7. Problemas de calidad esperados

Se mantienen los ya identificados en la entrega anterior (huecos esperados vs. fallos reales de descarga, ajustes por splits/dividendos, outliers legítimos en días de alta volatilidad, volumen anómalo, desactualización si falla el job diario). A estos se añade un riesgo de calidad específico de esta revisión:

- **Fuga de información entre features y target si no se separan físicamente**: si en algún momento del pipeline se recompone `gold_aapl_train` y `gold_aapl_inference` en una sola tabla "por comodidad", se reintroduce el riesgo de leakage que esta misma entrega busca eliminar por diseño.

## 8. Decisiones de limpieza y transformación previstas

Se mantienen las decisiones ya tomadas (no rellenar huecos de días no bursátiles, upsert por `ticker+date`, fechas ISO, uso de `Adj Close` para retornos, descarte de filas con precios/volumen inválidos). Se añaden las decisiones que cierran el contrato de evaluación pedido:

- **Separación física features/label**: `gold_aapl_inference` no contiene columnas de label ni siquiera vacías; el modelo nunca puede recibir `close_next_day` ni `target_up_down` como entrada porque esas columnas no existen en ese dataset.
- **Sin lookahead en las features**: las medias móviles y la volatilidad de un día `t` se calculan únicamente con datos de días ≤ `t`; no se usan ventanas centradas ni datos futuros.
- **División temporal, nunca aleatoria**: el train/test de `gold_aapl_train` se separa cronológicamente (por ejemplo, los últimos 6-12 meses como test, el resto como train), nunca mediante un `train_test_split` aleatorio, dado que las observaciones están autocorrelacionadas en el tiempo y un split aleatorio filtraría información del futuro hacia el pasado. Se contempla, como mejora sobre el MVP, una validación walk-forward (ventana expansiva) en vez de un único corte.
- **Baselines obligatorios**: toda evaluación del modelo se acompaña de al menos dos baselines simples calculados sobre el mismo split temporal: (1) predecir siempre la clase mayoritaria del train, y (2) repetir la tendencia del día anterior (persistencia). El modelo solo se considera útil si mejora a ambos de forma consistente en el periodo de test, no solo en el conjunto de entrenamiento.
- **Persistencia obligatoria de predicciones**: cada predicción generada en producción (no solo en backtest) se guarda en la tabla `predictions` junto con la versión del modelo, precisamente para poder comparar el rendimiento real acumulado frente al rendimiento de backtest y detectar degradación del modelo con el tiempo.

## 9. Riesgos del modelo de datos

- **Parte más clara**: el esquema de capas (`raw` → `daily_prices` → `gold_aapl_train`/`gold_aapl_inference` → `predictions`) y la separación física entre features y labels, que elimina por diseño el riesgo de leakage detectado en el prototipo inicial.
- **Parte que genera más incertidumbre**: la validación walk-forward completa (frente a un único corte temporal) es más robusta pero también más costosa de implementar; se decidirá durante el desarrollo si el tiempo del curso permite ir más allá de un holdout cronológico simple.
- **Fuente/tabla que puede dar más problemas**: `daily_prices`, por la dependencia de `yfinance` (no oficial); cualquier fallo en el pull diario se propaga a `gold_aapl_inference` y por tanto a la predicción del día.
- **Qué ocurriría si no se puede sostener la separación física train/inferencia**: como alternativa mínima, se mantendría una sola tabla gold con el target a `NULL` en la última fila, pero se añadiría una validación explícita en el código de entrenamiento que rechace la ejecución si detecta `close_next_day` o `target_up_down` entre las columnas de `X` — una salvaguarda en software en vez de en el propio modelo de datos.
- **Contrato de evaluación (cierre explícito)**: el valor del proyecto no está en prometer una señal rentable, sino en una validación temporal honesta, sin leakage, frente a baselines simples. Un resultado donde el modelo no supere a los baselines es un resultado válido y debe reportarse como tal, no ocultarse ni maquillarse.
