# Cambios de la auditoría (16/09/2026)

Esta carpeta (`stocker_project_v2`) es una copia de `stocker_project` con las
correcciones de la auditoría aplicadas. El proyecto original no se ha tocado.

Alcance: hallazgos críticos (C1–C3), altos (A1–A5) y medios (M1–M5), más aviso
legal, fecha de datos y enlaces a noticias (U1, U2, U4). El resto de mejoras de
experiencia de usuario e ingeniería queda para una segunda tanda (ver al final).

## Pasos para dejar la copia al día

Ejecuta todo desde la carpeta de la copia, con el mismo entorno de Python que
usas para el proyecto original:

```bat
cd "C:\Users\imsmo\Documents\Claude Projects\stocker_project_v2"
```

| # | Comando | Para qué | Tiempo aprox. |
|---|---------|----------|---------------|
| 1 | `pip install pytest` y `python -m pytest tests -q` | Comprobar que las correcciones funcionan en tu equipo (9 pruebas, sin tocar datos). | < 1 min |
| 2 | `python src\maintenance.py repair` | Copia de seguridad en `data\backups\` y reconstrucción de `gold_train`/`gold_inference` (índice corrupto, C1). | 1 min |
| 3 | `python src\load.py --refresh-all` | Recarga el histórico completo de los 207 tickers: corrige el split de APH, los dividendos y los cierres de media sesión ya guardados (C2, C3). | 10–15 min |
| 4 | `python src\gold.py` | Recalcula features y targets (ahora sobre `adj_close`, M3). | 1 min |
| 5 | `python src\track_predictions.py --reresolve` | Recalcula los resultados reales de todas las predicciones con cierres definitivos (C2). | < 1 min |
| 6 | `python src\model.py --horizon 1`, luego `--horizon 5` y `--horizon 20` | Reentrena los tres modelos con los datos corregidos, sin features de noticias, con baselines por horizonte, AUC e importancia por permutación. | 5–10 min cada uno |
| 7 | `python src\run_pipeline.py` **después de las 22:30** | Primera ejecución completa: predicciones de día, semana y mes. | 5 min |
| 8 | Programador de tareas (ver abajo) | Dejarlo automático. | — |

Opcionales:

- `python src\model.py --horizon 1 --class-weight none`: compara con el modelo
  del paso 6. `balanced` resta accuracy frente al baseline mayoritario (M1).
- `python src\backtest_walkforward.py --horizon 1` (y 5, 20): acierto año a año
  frente a los baselines, guardado en `data\reports\`. Con Random Forest tarda
  bastante; con `--model logistic`, un par de minutos.
- Demo publicada (A4): `python src\export_demo_db.py`, borra los `.joblib`
  antiguos de `models_demo\` y copia allí los tres modelos nuevos del paso 6.

### Programador de tareas

- **Una sola tarea** que ejecute `run_daily_pipeline.bat` de esta carpeta a las
  **22:30**, de lunes a sábado. Las noticias ya van incluidas al final. Los
  sábados hace la recarga completa del histórico.
- La tarea de `run_news_daily.bat` ya no hace falta.
- **No dejes activas a la vez las tareas del original y las de la copia:**
  comparten la clave de Alpha Vantage (25 llamadas al día) y agotarían el cupo
  entre las dos. Cuando pases a usar la copia, desactiva las del original.

## Qué ha cambiado, por hallazgo

### Críticos

**C1 · Base de datos corrupta y escrituras simultáneas**
- `src/maintenance.py` (nuevo): `check`, `backup` (API de backup de SQLite,
  conserva las 5 últimas), `repair` y `vacuum`.
- `src/run_pipeline.py` (nuevo): toda la cadena diaria en un proceso, con
  cerrojo (`src/pipeline_lock.py`), `PRAGMA quick_check` antes de escribir,
  marcas de tiempo, resumen y `data/pipeline_status.json`.
- `src/db.py`: modo WAL y `busy_timeout` de 30 s. `news.py` usa el mismo
  cerrojo, así que ya no escribe a la vez que el pipeline.
- `src/model.py`: descarta filas duplicadas de `gold_train` con aviso.
- `run_daily_pipeline.bat` llama a `run_pipeline.py`.

**C2 · Cierres de media sesión**
- `src/market_time.py` (nuevo): última sesión cerrada según el calendario NYSE
  real, con 20 minutos de margen tras el cierre.
- `src/load.py`: descarta las sesiones que aún no han cerrado.
- `src/track_predictions.py`: solo resuelve sesiones cerradas; `--reresolve`
  recalcula las ya resueltas.
- `src/predict.py`: no guarda predicciones si la sesión objetivo ya abrió
  (`--allow-late` para forzarlo).

**C3 · Splits y dividendos**
- `src/load.py`: si las fechas descargadas no coinciden con lo guardado (o, sin
  solape, el CSV trae un split o dividendo), re-descarga y sustituye el
  histórico completo del ticker. `--refresh TICKER` y `--refresh-all` a mano.
- `src/run_pipeline.py`: recarga completa automática los sábados.

### Altos

**A1 · Semana y mes sin actualizar** — `predict.py --all-horizons`, usado por el
pipeline diario. Las tarjetas de predicción indican cuándo y con qué modelo se
hizo cada una.

**A2 · Tickers deslistados** — EA fuera de `config.TICKERS`. `predict.py` solo
predice filas fechadas en la última sesión cerrada y lista las excluidas. El
dashboard oculta los tickers con más de `STALE_TICKER_DAYS` (7) días de retraso.

**A3 · Cobertura de noticias** — tabla `news_fetch_log`: cada ticker se pide
desde su última consulta hasta hoy (máximo 35 días), empezando por los que más
tiempo llevan sin consultarse. Se para al agotar el cupo. Las features de
noticias salen del modelo (`MODEL_USE_NEWS_FEATURES = False`). El medidor de
sentimiento dice cuántas acciones cubre.

**A4 · Demo** — aviso «Datos de mercado hasta…» y estado de la última
actualización en Dashboard y Predicciones. `export_demo_db.py` crea la demo sin
WAL (un solo fichero para git). Falta regenerarla tú (pasos opcionales).

**A5 · Chat Premium y secretos**
- Corpus en bloque de sistema con `cache_control` (prompt caching).
- Sin 10-Q y con tope de 400.000 caracteres; historial limitado a 10 mensajes.
- Límite diario por IP además de sesión y global; contador con escritura
  atómica. Los errores de la API ya no entran en el historial.
- `news.py` enmascara la clave de Alpha Vantage en los mensajes de error.

### Medios

**M1 · Evaluación** — persistencia con el retorno del mismo plazo que el
horizonte; AUC, log loss y Brier; `--class-weight`;
`src/backtest_walkforward.py`; referencia «siempre sube» en «Día a día».

**M2 · Importancia de features** — importancia por permutación sobre el test,
guardada en el modelo; selector de horizonte en «En qué se fija».

**M3 · Target** — `gold.py` y `track_predictions.py` usan `adj_close`.

**M4 · Selección de modelo** — el más reciente por fecha del nombre, no por
orden alfabético; las páginas recargan el modelo al reentrenar; «Predicciones»
avisa si la predicción guardada es de otro modelo. `predict.py` usa las
columnas con que se entrenó cada modelo, así que los antiguos (16 features) y
los nuevos (14) conviven.

**M5 · Resolución de predicciones** — nueva columna `predictions.date_origen`
(se añade sola a la base de datos existente); la resolución usa esa fecha. Un
upsert ya no borra resultados ya calculados.

### Experiencia de usuario

- **U1**: aviso legal en todas las páginas.
- **U2**: aciertos y fallos del periodo de entrenamiento en gris en Dashboard y
  Predicciones.
- **U4**: titulares enlazados al artículo en Dashboard y «Noticias de la
  acción».

## Verificación hecha antes de entregar

Sobre una copia de `stocker.db` del 16/09 (en la nube, sin tocar tu equipo), con
scikit-learn 1.5.2 (la versión con la que se entrenaron tus modelos) y pandas 2.2:

- `maintenance.py repair`: de 8.306 filas fuera del índice a `integrity_check: ok`.
- `track_predictions.py --reresolve`: 4.544 predicciones recalculadas.
- `predict.py`: excluye tickers desfasados y se niega a predecir con la sesión
  abierta; con `--allow-late`, 207 predicciones por horizonte con los modelos
  del 09/09.
- `model.py`: entrenamiento completo del horizonte día con las métricas e
  importancias nuevas.
- Las 8 páginas del dashboard cargan sin errores con `AppTest`.
- `pytest`: 9 pruebas en verde.

No se pudo probar la descarga real desde la nube (Yahoo Finance bloquea ese
acceso): los pasos 3 y 7 son la primera ejecución real con yfinance.

## Pendiente para una segunda tanda

U3 (ticker compartido entre páginas), U5 (navegación agrupada), U6 (panel de
salud completo; ya hay una línea de estado), U7 (Comparador con `adj_close`,
correlación y panel técnico), E1 (versiones fijadas y CI; ya hay pruebas),
E2 (docstrings como bitácora), E3 (consultas del dashboard), E4 (rotación de
logs y avisos), E5 (`data/raw`), E6 (resto de documentación: `src/README.md`,
`dashboard/README.md`, `CONTEXTO.md`) y E7 (sector de FISV, licencia).
