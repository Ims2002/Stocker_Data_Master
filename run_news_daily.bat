@echo off
REM Lanza src\news.py en modo automático (backfill si queda pendiente, si
REM no, actualización diaria) y guarda la salida en data\news_cron.log.
REM Pensado para programarse con el Task Scheduler de Windows (una vez al
REM día) — ver CONTEXTO.md / src/README.md para el detalle.
REM
REM PYTHONUTF8=1 fuerza a Python a escribir stdout/stderr en UTF-8 pase lo
REM que pase con el codepage de la consola del Task Scheduler — sin esto,
REM las tildes y rayas de los mensajes salían como caracteres sueltos
REM ilegibles (mojibake) en el .log (detectado 2026-07-31).
set PYTHONUTF8=1
cd /d "%~dp0"
python src\news.py >> data\news_cron.log 2>&1
