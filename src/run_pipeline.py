"""
src/run_pipeline.py — cadena diaria completa, en orden y sin solaparse.

Sustituye la lista de comandos sueltos de `run_daily_pipeline.bat`
(auditoría del 16/09/2026). Qué añade respecto a antes:

- Un único proceso con cerrojo (`pipeline_lock`): la actualización de
  noticias ya no escribe en la base de datos a la vez que el pipeline de
  precios (C1). Noticias va al final.
- `PRAGMA quick_check` antes de escribir: si la base de datos está dañada,
  se para y lo dice, en vez de seguir escribiendo encima (C1).
- Aviso si se ejecuta con la bolsa abierta (C2). `load.py` descarta de todos
  modos las sesiones sin cerrar y `predict.py` no guarda predicciones con la
  sesión objetivo en marcha.
- Predicciones de los tres horizontes, no solo del día (A1).
- Los sábados, recarga completa del histórico de todos los tickers en lugar
  de la descarga corta, como red de seguridad ante splits y dividendos (C3).
- Marca de tiempo por paso, resumen final y `data/pipeline_status.json` con
  el resultado de cada paso (el dashboard lo muestra en el pie).

Uso:
    python src/run_pipeline.py                  # lo que ejecuta la tarea programada
    python src/run_pipeline.py --full-refresh   # fuerza la recarga completa del histórico
    python src/run_pipeline.py --skip-news
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod  # noqa: E402
import market_time  # noqa: E402
from config import DATA_DIR  # noqa: E402
from pipeline_lock import LockTimeout, pipeline_lock  # noqa: E402

STATUS_PATH = DATA_DIR / "pipeline_status.json"
FULL_REFRESH_WEEKDAY = 5  # sábado (lunes = 0)


def _log(msg: str) -> None:
    print(f"[pipeline {dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def _run_step(name: str, func, *args) -> dict:
    _log(f"inicio: {name}")
    started = dt.datetime.now()
    try:
        rc = func(*args)
        rc = int(rc or 0)
        error = None
    except SystemExit as exc:  # argparse u otros SystemExit dentro de un main()
        rc, error = int(exc.code or 0), None
    except Exception as exc:  # noqa: BLE001
        rc, error = 99, f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    secs = (dt.datetime.now() - started).total_seconds()
    _log(f"fin: {name} (código {rc}, {secs:.0f} s){' — ' + error if error else ''}")
    return {"step": name, "rc": rc, "seconds": round(secs), "error": error}


def _write_status(status: dict) -> None:
    try:
        STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except OSError as exc:
        _log(f"no se pudo escribir {STATUS_PATH}: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--full-refresh", action="store_true", help="recarga completa del histórico de todos los tickers")
    parser.add_argument("--skip-news", action="store_true", help="no ejecutar la actualización de noticias")
    args = parser.parse_args(argv)

    import download
    import gold
    import load
    import news
    import predict
    import track_predictions

    status: dict = {"started_at": dt.datetime.now().isoformat(timespec="seconds"), "steps": []}
    try:
        with pipeline_lock("run_pipeline"):
            engine = dbmod.get_engine()
            problems = dbmod.quick_check(engine)
            if problems:
                _log("la base de datos NO pasa quick_check — no se escribe nada. Ejecuta "
                     "`python src/maintenance.py repair`. Primeros errores: " + "; ".join(problems[:3]))
                status.update(result="database_corrupt", finished_at=dt.datetime.now().isoformat(timespec="seconds"))
                _write_status(status)
                return 2

            if market_time.market_is_open():
                _log("AVISO: la bolsa de EE. UU. está abierta. Se descartarán las sesiones sin cerrar y no se "
                     "guardarán predicciones; programa la tarea después de las 22:20 (hora de Madrid).")
            status["last_closed_session"] = str(market_time.last_closed_session())

            full = args.full_refresh or dt.date.today().weekday() == FULL_REFRESH_WEEKDAY
            steps = []
            if full:
                steps.append(("recarga completa del histórico", load.main, ["--refresh-all"]))
            else:
                steps.append(("descarga diaria", download.main, ["--daily"]))
                steps.append(("carga en daily_prices", load.main, []))
            steps += [
                ("capa gold", gold.main, None),
                ("predicciones (día, semana y mes)", predict.main, ["--all-horizons"]),
                ("resolución de predicciones", track_predictions.main, []),
            ]
            if not args.skip_news:
                steps.append(("noticias", news.main, ["--no-lock"]))

            for name, func, step_args in steps:
                status["steps"].append(_run_step(name, func, step_args))
                _write_status(status)
    except LockTimeout as exc:
        _log(f"no se ejecuta: {exc}")
        return 3

    failed = [s for s in status["steps"] if s["rc"] != 0]
    status["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
    status["result"] = "ok" if not failed else "with_errors"
    _write_status(status)
    _log("resumen: " + ", ".join(f"{s['step']}={s['rc']}" for s in status["steps"]))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
