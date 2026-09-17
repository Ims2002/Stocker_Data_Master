"""
src/maintenance.py — copia de seguridad, verificación y reparación de stocker.db.

Nace de la auditoría del 16/09/2026 (hallazgo C1): `PRAGMA integrity_check`
encontró el índice de `gold_train` corrupto (8.306 filas fuera del índice),
lo que provocaba `database disk image is malformed` y `UNIQUE constraint
failed` en `gold.py`, filas duplicadas en `gold_train` y features de
inferencia congeladas para algunos tickers.

Las tablas `gold_train` y `gold_inference` son 100 % derivadas de
`daily_prices` (+ noticias), así que la reparación segura es eliminarlas y
regenerarlas con `gold.py`. Las tablas de origen (`daily_prices`,
`news_articles`, `predictions`, `stocks`) no se tocan.

Uso:
    python src/maintenance.py check          # integrity_check completo (sale con 1 si hay errores)
    python src/maintenance.py backup         # copia consistente en data/backups/
    python src/maintenance.py repair         # backup + reconstruye gold_train/gold_inference + check
    python src/maintenance.py vacuum         # compacta el fichero (tras repair)
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod  # noqa: E402
from config import DATA_DIR, DB_PATH  # noqa: E402

BACKUP_DIR = DATA_DIR / "backups"
# Cuántas copias de seguridad automáticas se conservan (las más antiguas se borran).
MAX_BACKUPS = 5


def integrity_check(db_path: Path = DB_PATH, max_errors: int = 50) -> list[str]:
    """`PRAGMA integrity_check` completo (más lento que `quick_check`, pero
    detecta también índices que no coinciden con su tabla). Lista vacía si
    todo está bien."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = [r[0] for r in con.execute(f"PRAGMA integrity_check({max_errors})")]
    finally:
        con.close()
    return [] if rows == ["ok"] else rows


def backup(db_path: Path = DB_PATH) -> Path:
    """Copia consistente con la API de backup de SQLite (incluye lo que haya
    en el fichero -wal, a diferencia de copiar el .db a mano)."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"{db_path.stem}_{dt.datetime.now():%Y%m%d_%H%M%S}.db"
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    print(f"[maintenance] copia de seguridad: {dest}", file=sys.stderr)

    backups = sorted(BACKUP_DIR.glob(f"{db_path.stem}_*.db"))
    for old in backups[:-MAX_BACKUPS]:
        old.unlink()
        print(f"[maintenance] eliminada copia antigua: {old.name}", file=sys.stderr)
    return dest


def rebuild_gold_tables() -> int:
    """Elimina gold_train/gold_inference y las regenera desde daily_prices."""
    import gold

    con = sqlite3.connect(str(DB_PATH))
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("DROP TABLE IF EXISTS gold_train")
        con.execute("DROP TABLE IF EXISTS gold_inference")
        con.commit()
    finally:
        con.close()
    print("[maintenance] gold_train y gold_inference eliminadas; regenerando con gold.py…", file=sys.stderr)
    return gold.main()


def vacuum() -> None:
    con = sqlite3.connect(str(DB_PATH))
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("VACUUM")
    finally:
        con.close()
    print("[maintenance] VACUUM completado", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["check", "backup", "repair", "vacuum"])
    args = parser.parse_args(argv)

    if not DB_PATH.exists():
        print(f"[maintenance] no existe {DB_PATH}", file=sys.stderr)
        return 1

    if args.action == "check":
        errors = integrity_check()
        if not errors:
            print("[maintenance] integrity_check: ok", file=sys.stderr)
            return 0
        print(f"[maintenance] integrity_check: {len(errors)} problema(s) (se muestran hasta 50):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    if args.action == "backup":
        backup()
        return 0

    if args.action == "vacuum":
        vacuum()
        return 0

    # repair
    backup()
    before = integrity_check()
    print(f"[maintenance] antes de reparar: {'ok' if not before else f'{len(before)} problema(s)'}", file=sys.stderr)
    failed = rebuild_gold_tables()
    after = integrity_check()
    if after:
        print(
            "[maintenance] siguen quedando problemas fuera de gold_*: restaura la copia de seguridad o "
            "exporta con `sqlite3 stocker.db \".recover\"`. Detalle:",
            file=sys.stderr,
        )
        for e in after:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"[maintenance] integrity_check tras reparar: ok ({failed} ticker(s) con fallo en gold.py)", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
