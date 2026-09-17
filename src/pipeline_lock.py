"""
src/pipeline_lock.py — evita que dos procesos escriban en stocker.db a la vez.

Auditoría del 16/09/2026 (C1): `run_news_daily.bat` y
`run_daily_pipeline.bat` arrancaban con un minuto de diferencia, el log
registraba `database is locked` y el índice de `gold_train` acabó corrupto.
Este cerrojo es un fichero creado de forma atómica: si ya existe, el
segundo proceso espera (hasta `timeout`) en vez de escribir en paralelo. Un
cerrojo de más de `stale_after` segundos se considera abandonado (proceso
que murió sin borrarlo) y se reemplaza.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DATA_DIR  # noqa: E402

LOCK_PATH = DATA_DIR / ".pipeline.lock"


class LockTimeout(RuntimeError):
    pass


@contextlib.contextmanager
def pipeline_lock(name: str, timeout: float = 3 * 3600, stale_after: float = 4 * 3600, poll: float = 30):
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    announced = False
    while True:
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(f"{name} pid={os.getpid()} {dt.datetime.now().isoformat()}\n")
            break
        except FileExistsError:
            try:
                age = time.time() - LOCK_PATH.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > stale_after:
                print(f"[lock] cerrojo abandonado ({age / 3600:.1f} h) — se reemplaza", file=sys.stderr)
                with contextlib.suppress(FileNotFoundError):
                    LOCK_PATH.unlink()
                continue
            if time.monotonic() - start > timeout:
                raise LockTimeout(f"otro proceso mantiene {LOCK_PATH} desde hace {age / 60:.0f} min")
            if not announced:
                holder = LOCK_PATH.read_text(encoding="utf-8", errors="replace").strip()
                print(f"[lock] {name}: esperando a que termine otro proceso ({holder})", file=sys.stderr)
                announced = True
            time.sleep(poll)
    try:
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            LOCK_PATH.unlink()
