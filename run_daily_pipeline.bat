@echo off
REM Cadena diaria completa de Stocker (precios, gold, predicciones de los
REM tres horizontes, resolucion de predicciones y noticias) en UN solo
REM proceso con cerrojo - ver src\run_pipeline.py.
REM
REM PROGRAMACION RECOMENDADA (auditoria 16/09/2026):
REM   - Una unica tarea diaria a las 22:30 hora de Madrid, de lunes a sabado.
REM     La bolsa de EE. UU. cierra a las 22:00 (hora de Madrid); ejecutar
REM     antes guarda precios de media sesion (hallazgo C2).
REM   - Desactivar la tarea de run_news_daily.bat: las noticias ya van
REM     incluidas al final de esta cadena. Si se mantiene, espera a que
REM     termine el pipeline gracias al cerrojo, pero no hace falta.
REM   - Los sabados se hace la recarga completa del historico (splits y
REM     dividendos, hallazgo C3).
REM
REM PYTHONUTF8=1 evita el mojibake en el log (ver CONTEXTO.md, 2026-07-31).
set PYTHONUTF8=1
cd /d "%~dp0"
python src\run_pipeline.py >> data\pipeline_cron.log 2>&1
exit /b %ERRORLEVEL%
