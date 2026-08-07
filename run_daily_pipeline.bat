@echo off
REM Lanza la cadena diaria de precios/modelo/seguimiento y guarda la salida
REM en data\pipeline_cron.log. Pensado para programarse con el Task
REM Scheduler de Windows (una vez al dia, antes o despues de
REM run_news_daily.bat) - ver CONTEXTO.md / src/README.md para el detalle.
REM
REM Orden (cada paso es independiente y sigue aunque el anterior haya
REM fallado para algunos tickers - ver docstrings de cada script):
REM   1. download.py --daily  - descarga solo los ultimos dias (NO el
REM                             historico completo de 10 anios - ver
REM                             config.DOWNLOAD_DAILY_LOOKBACK_DAYS)
REM   2. load.py              - upsert en daily_prices (dedupe automatico
REM                             si hay solape con lo ya cargado)
REM   3. gold.py              - recalcula gold_train/gold_inference
REM   4. predict.py           - genera la prediccion de manana con el
REM                             modelo mas reciente en models\
REM   5. track_predictions.py - resuelve predicciones anteriores con el
REM                             cierre real ya conocido en daily_prices
REM
REM PYTHONUTF8=1 evita el mojibake en el log (mismo motivo que
REM run_news_daily.bat, ver CONTEXTO.md, 2026-07-31).
set PYTHONUTF8=1
cd /d "%~dp0"
python src\download.py --daily >> data\pipeline_cron.log 2>&1
python src\load.py >> data\pipeline_cron.log 2>&1
python src\gold.py >> data\pipeline_cron.log 2>&1
python src\predict.py >> data\pipeline_cron.log 2>&1
python src\track_predictions.py >> data\pipeline_cron.log 2>&1
