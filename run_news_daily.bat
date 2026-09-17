@echo off
REM Lanza src\news.py en modo automatico (backfill si queda pendiente, si
REM no, actualizacion diaria) y guarda la salida en data\news_cron.log.
REM
REM Desde la auditoria del 16/09/2026 las noticias ya van incluidas al final
REM de run_daily_pipeline.bat, asi que esta tarea es opcional. Si se usa,
REM news.py espera a que termine el pipeline (cerrojo compartido) en vez de
REM escribir en la base de datos a la vez.
REM
REM PYTHONUTF8=1 fuerza a Python a escribir stdout/stderr en UTF-8 pase lo
REM que pase con el codepage de la consola del Task Scheduler (detectado
REM 2026-07-31).
set PYTHONUTF8=1
cd /d "%~dp0"
python src\news.py >> data\news_cron.log 2>&1
exit /b %ERRORLEVEL%
