# Entrega 3 — Diseño del modelo de datos y capa gold del proyecto

## 1. Resumen de la idea y datos del proyecto

**Stocker** aborda la falta de una herramienta propia, sencilla y sin coste, que permita a un inversor particular hacer seguimiento del histórico de una acción concreta (AAPL en el MVP) y disponer de una señal orientativa diaria sobre si el precio subirá o bajará al día siguiente. La solución construye un histórico diario de precios (Open, High, Low, Close, Adj Close, Volume) y entrena un modelo de clasificación binaria cuya variable objetivo se obtiene desplazando el Close un día hacia atrás (shift -1) y comparándolo con el Close actual.

La única fuente de datos es la **API de Yahoo Finance**, accedida mediante la librería `yfinance`, que aporta el histórico diario de cotización de la acción desde su salida a bolsa. Esta fuente cubre toda la información necesaria (precio y volumen); no se incorporan fuentes adicionales en el MVP (noticias, datos macro, sentimiento), que quedan como posibles ampliaciones futuras.

## 2. Tecnología o formato de almacenamiento elegido

Se adopta una combinación de dos formatos, cada uno con un propósito distinto:

- **CSV como capa de intercambio inicial (raw)**: `yfinance` devuelve los datos como DataFrame, que se vuelca a CSV tal cual llega de la API. Tiene sentido mantener este primer volcado en CSV porque actúa como copia de auditoría de "qué se descargó y cuándo", es el formato nativo de salida de la librería y no requiere ninguna infraestructura adicional.
- **Base de datos SQL (SQLite) para processed y gold**: a partir del CSV crudo, los datos se cargan en una base de datos SQL. Se elige **SQLite** para el MVP por ser suficiente para el volumen esperado (unos pocos miles de filas), no requerir servidor ni mantenimiento, e integrarse directamente con pandas/SQLAlchemy. Si el proyecto escala a varias acciones más adelante, la migración a **PostgreSQL** en el mismo VPS de Hetzner ya utilizado para otros proyectos es inmediata, ya que el esquema no cambiaría.

No se contemplan Excel (no aporta nada frente a CSV/SQL para este caso) ni Parquet (el volumen de datos no lo justifica en el MVP; se revisará si el proyecto crece a decenas de acciones con años de histórico).

## 3. Estructura de capas de datos

```
data/
├── raw/
│   └── aapl_YYYYMMDD.csv        # volcado tal cual de la API, uno por descarga
├── processed/
│   └── daily_prices (tabla SQL) # histórico limpio, tipado y sin duplicados
└── gold/
    └── gold_aapl_daily (tabla/vista SQL) # dataset final con features y target
```

| Capa | Contenido esperado |
|---|---|
| **Raw** | CSV con los datos tal como los devuelve `yfinance` (Date, Open, High, Low, Close, Adj Close, Volume), sin ninguna transformación. Cada descarga diaria se guarda como un fichero independiente, a modo de log/auditoría. |
| **Processed** | Tabla `daily_prices` en SQL: mismos campos que el raw, pero con tipos de datos correctos (fechas como `date`, precios como `float`, volumen como `int`), fechas normalizadas a ISO, duplicados resueltos mediante upsert por `(ticker, date)`, y sin huecos que no correspondan a días no bursátiles. |
| **Gold** | Tabla `gold_aapl_daily` en SQL: una fila por día de cotización, con las variables derivadas (retornos, medias móviles, volatilidad) y la variable objetivo (`target_up_down`) ya calculadas y listas para el modelo y el dashboard. |

## 4. Definición de la capa gold

| Dataset gold | Granularidad | Campos clave | Uso posterior |
|---|---|---|---|
| `gold_aapl_daily` | Una fila por día de cotización de AAPL | `ticker`, `date`, `close`, `return_1d`, `ma_5/10/20`, `volatility_10d`, `target_up_down` | Entrenamiento/evaluación del modelo de clasificación y alimentación del dashboard histórico |

**Descripción funcional**: dataset final con el histórico diario de AAPL enriquecido con variables derivadas y la variable objetivo ya calculada, listo para ser consumido directamente por el modelo y por la visualización, sin necesidad de transformaciones adicionales.

**Nivel de granularidad**: un registro por día de cotización (día bursátil) de la acción.

**Número aproximado de registros**: entre ~1.250 (5 años de histórico) y ~2.500 (10 años), según la profundidad histórica final escogida al implementar el proyecto.

**Campos principales:**

| Campo | Tipo de dato |
|---|---|
| `ticker` | string (fijo "AAPL" en el MVP) |
| `date` | date |
| `open`, `high`, `low`, `close`, `adj_close` | float |
| `volume` | integer |
| `return_1d` | float (retorno porcentual respecto al día anterior) |
| `ma_5`, `ma_10`, `ma_20` | float (medias móviles del cierre) |
| `volatility_10d` | float (desviación estándar de retornos en ventana de 10 días) |
| `close_next_day` | float (Close desplazado -1; campo auxiliar, no se usa como feature) |
| `target_up_down` | integer (0/1) |

**Clave primaria**: `(ticker, date)`.

**Variable objetivo**: `target_up_down` (clasificación binaria: 1 si `close_next_day` > `close`, 0 en caso contrario). De forma secundaria, `close_next_day` puede usarse como objetivo auxiliar de regresión si se explora esa vía.

**Fase posterior que lo consume**: entrenamiento y evaluación del modelo de clasificación, y el dashboard/notebook que compara el histórico real con las predicciones del modelo.

## 5. Relaciones entre datos

En el MVP el proyecto trabaja con una única acción, por lo que el modelo relacional es intencionadamente simple, pero se deja preparado para escalar:

- **`stocks`** (dimensión: `ticker` PK, nombre, sector) — en el MVP contendrá una única fila (AAPL), pero permite añadir más acciones sin rediseñar el esquema.
- **`daily_prices`** (capa processed: histórico limpio) — PK `(ticker, date)`.
- **`gold_aapl_daily`** (capa gold: dataset final) — PK `(ticker, date)`.
- **`predictions`** (opcional, para llevar histórico de aciertos del modelo: `ticker`, `date`, `predicción`, `probabilidad`, `versión_modelo`).

Relaciones:

```
stocks.ticker            1 --- N   daily_prices.ticker
daily_prices.(ticker,date) 1 --- 1 gold_aapl_daily.(ticker,date)
gold_aapl_daily.(ticker,date) 1 --- N predictions.(ticker,date)   (si se versionan varios modelos por día)
```

`gold_aapl_daily` es una transformación fila a fila de `daily_prices` (no cruza distintos tickers en el MVP), por lo que no hay joins complejos N:M. Si en el futuro se incorporan varias acciones, el principal problema a vigilar al combinar datos sería asegurar que los calendarios de cotización coincidan entre ellas (para acciones de EE.UU. comparten el mismo calendario NYSE/NASDAQ, por lo que no se anticipa un problema real por ahora).

## 6. Diccionario de datos inicial

| Campo | Descripción | Tipo de dato | Fuente | Obligatorio | Observaciones |
|---|---|---|---|---|---|
| `ticker` | Símbolo bursátil de la acción | string | Yahoo Finance (`yfinance`) | Sí | Fijo "AAPL" en el MVP |
| `date` | Fecha de la sesión de cotización | date | Yahoo Finance | Sí | Formato YYYY-MM-DD; solo días bursátiles |
| `open` | Precio de apertura | float | Yahoo Finance | Sí | — |
| `close` | Precio de cierre | float | Yahoo Finance | Sí | Base para el retorno y el target |
| `adj_close` | Cierre ajustado por splits/dividendos | float | Yahoo Finance | Sí | Usar como base de cálculo de retornos para no romper la serie |
| `high` / `low` | Máximo / mínimo de la sesión | float | Yahoo Finance | Deseable | No usados como feature en el MVP inicial |
| `volume` | Volumen negociado | integer | Yahoo Finance | Sí | Puede requerir revisión si aparece en 0 |
| `return_1d` | Retorno diario | float | Derivado | Sí | Calculado sobre `adj_close` |
| `ma_5` / `ma_10` / `ma_20` | Medias móviles del cierre | float | Derivado | Deseable | Ventanas de 5/10/20 días |
| `volatility_10d` | Volatilidad reciente | float | Derivado | Deseable | Desviación típica de `return_1d` en ventana de 10 días |
| `target_up_down` | Variable objetivo (sube/baja) | integer (0/1) | Derivado | Sí | 1 si `close` de mañana > `close` de hoy |

## 7. Problemas de calidad esperados

- **Huecos "esperados" vs. huecos por fallo real**: los fines de semana y festivos de mercado (NYSE/NASDAQ) no tienen cotización, lo cual es normal y no debe tratarse como dato faltante. El reto de calidad real es distinguir estos huecos esperados de un hueco causado por un fallo en la descarga diaria (fallo de red, cambio en `yfinance`, mantenimiento de la API).
- **Ajustes por splits y dividendos**: tras un split, el `Close` histórico deja de ser comparable si no se usa `Adj Close`; no tenerlo en cuenta rompería la continuidad de la serie y distorsionaría los retornos calculados.
- **Outliers legítimos**: días de alta volatilidad (publicación de resultados trimestrales, anuncios relevantes) pueden generar retornos extremos que no son errores de datos sino movimientos reales del mercado; conviene no filtrarlos automáticamente como si fueran ruido.
- **Volumen anómalo**: posibles valores de `Volume` en cero o inconsistentes en algún día concreto, que habría que validar contra la fuente antes de usarlos.
- **Datos desactualizados**: si el job de descarga diaria falla un día sin que se detecte, la tabla `processed` queda desactualizada y ese desfase se propaga directamente a `gold`.
- **Cobertura histórica**: no se anticipa problema relevante en datos diarios (a diferencia de datos intradía, que sí tienen ventanas limitadas en la API gratuita), pero conviene verificarlo al implementar la descarga completa por primera vez.

## 8. Decisiones de limpieza y transformación previstas

- **Valores nulos**: no se rellenan los huecos correspondientes a días no bursátiles (se determinan comparando contra un calendario de mercado, por ejemplo con `pandas_market_calendars`); un hueco en un día que sí debería tener cotización se trata como fallo de descarga y dispara un reintento, no un relleno artificial.
- **Duplicados**: se resuelven mediante upsert en la carga a `daily_prices`, usando `(ticker, date)` como clave.
- **Fechas**: normalizadas a formato ISO (`YYYY-MM-DD`) y tipo `date`, sin componente horaria, dado que el proyecto trabaja a granularidad diaria.
- **Precios**: los retornos y medias móviles se calculan sobre `Adj Close`, no sobre `Close`, para no romper la serie ante splits o dividendos.
- **Variables derivadas**: retorno diario, medias móviles (5/10/20 días), volatilidad (desviación típica de retornos en ventana móvil) y la variable objetivo `target_up_down` mediante `shift(-1)` sobre el cierre.
- **Registros inválidos**: filas con `Open`/`Close` nulos o negativos, o `Volume` negativo, se descartan y se registran en un log de calidad para revisión manual.
- **Datos descartados**: no se descarta ninguna columna de las obtenidas de la API; `High`/`Low` se conservan en `processed` aunque no se usen como feature en la primera versión del modelo, quedando disponibles para iteraciones futuras.

## 9. Riesgos del modelo de datos

- **Parte más clara**: el esquema de tablas (`stocks` / `daily_prices` / `gold_aapl_daily`) y la clave primaria compuesta `(ticker, date)`, ya validados conceptualmente en la entrega anterior.
- **Parte que genera más incertidumbre**: la distinción automática entre "hueco esperado" (fin de semana/festivo) y "hueco por fallo de descarga", que requiere integrar correctamente un calendario de mercado y no es trivial de automatizar sin errores desde el primer intento.
- **Fuente/tabla que puede dar más problemas**: `daily_prices`, por depender de `yfinance`, una librería no oficial sensible a cambios en la web de Yahoo Finance; si el pull diario falla, el desfase se propaga a `gold`.
- **Qué ocurriría si no se puede construir la capa gold tal como está definida**: se simplificaría eliminando las medias móviles y la volatilidad, dejando solo el retorno diario y la variable `target_up_down`, que son las variables mínimas indispensables desde el planteamiento original del proyecto.
- **Alternativa para simplificar el modelo**: si la capa SQL diera más fricción de la esperada durante el desarrollo, se mantendría la misma estructura de capas (raw/processed/gold) implementada íntegramente sobre CSV, sacrificando las ventajas de upsert y de consultas transversales entre acciones, pero conservando el mismo contrato de datos de cara a las siguientes entregas.
