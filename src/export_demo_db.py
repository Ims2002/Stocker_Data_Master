"""
src/export_demo_db.py — genera data/stocker_demo.db: una copia reducida
de la base de datos real, con solo los tickers de
config.NEWS_PRIORITY_TICKERS (2026-08-27, ver CONTEXTO.md "Roadmap v1
(MVP para publicar)" y docs/ROADMAP_MVP.md). Pensado para la demo
publicada del v1: la base de datos real pesa ~254MB (no cabe en un repo
normal de GitHub, límite de 100MB/archivo); un subconjunto de 50
tickers pesa ~60MB.

Solo LEE `data/stocker.db` (o lo que apunte `config.DB_URL` en el
proceso que ejecuta este script) — nunca la modifica. Escribe un
fichero nuevo aparte, sobrescribiéndolo si ya existía de una ejecución
anterior. No copia `news_backfill_progress`: es estado interno del
backfill de noticias, el dashboard nunca la lee, no aporta nada a la
demo.

No hace falta reentrenar ningún modelo para la demo: el modelo no usa
el ticker como feature (ver model.py, build_feature_matrix), así que
los mismos `.joblib` entrenados sobre el universo completo sirven igual
para predecir sobre el subconjunto — ver `models_demo/` y
`config.MODELS_DIR` (override por `STOCKER_MODELS_DIR`).

Uso:
    python src/export_demo_db.py
    python src/export_demo_db.py --tickers NVDA,AAPL,MSFT   # subconjunto a medida
    python src/export_demo_db.py --out data/otra_demo.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, select

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod  # noqa: E402
from config import DATA_DIR, NEWS_PRIORITY_TICKERS  # noqa: E402

# Orden solo informativo para los logs — create_all() ordena por
# dependencias de FK automáticamente, no hace falta que esta lista
# respete el orden real de creación.
_TABLES = [
    dbmod.stocks,
    dbmod.daily_prices,
    dbmod.gold_train,
    dbmod.gold_inference,
    dbmod.predictions,
    dbmod.news_articles,
]


def export_demo_db(tickers: list[str], out_path: Path) -> None:
    src_engine = dbmod.get_engine()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    # create_engine directo (no dbmod.get_engine()) a propósito: el
    # destino no necesita la pragma de FOREIGN KEYS activada (ver
    # dbmod._enable_sqlite_foreign_keys) — este script inserta tablas
    # completas en orden de dependencia dentro de una única transacción,
    # no hay escrituras parciales que proteger.
    #
    # dbmod.init_db() (no metadata.create_all(tables=_TABLES) a secas):
    # crea TODO el esquema, incluida la vista `news_sentiment_daily` (SQL
    # crudo, no forma parte de `metadata`, así que un create_all parcial
    # se la salta) — el dashboard la necesita (get_daily_sentiment_for_ticker
    # y el resumen de "Lectura rápida" en views/dashboard.py). Bug real
    # encontrado al verificar la primera versión de este script con
    # AppTest: "OperationalError: no such table: news_sentiment_daily".
    dst_engine = create_engine(f"sqlite:///{out_path}")
    # wal=False: la demo se versiona en git como un único fichero .db; en
    # modo WAL parte de los datos podría quedarse en un fichero -wal aparte.
    dbmod.init_db(dst_engine, wal=False)

    print(f"[export_demo_db] universo: {len(tickers)} tickers", file=sys.stderr)

    with src_engine.begin() as src_conn, dst_engine.begin() as dst_conn:
        for table in _TABLES:
            query = select(table)
            if "ticker" in table.c:
                query = query.where(table.c.ticker.in_(tickers))
            rows = src_conn.execute(query).fetchall()
            if not rows:
                print(f"[export_demo_db] {table.name}: 0 filas", file=sys.stderr)
                continue
            dst_conn.execute(table.insert(), [dict(r._mapping) for r in rows])
            print(f"[export_demo_db] {table.name}: {len(rows)} filas copiadas", file=sys.stderr)

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"[export_demo_db] listo: {out_path} ({size_mb:.1f} MB)", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tickers", help="lista separada por comas (por defecto, config.NEWS_PRIORITY_TICKERS)"
    )
    parser.add_argument("--out", default=str(DATA_DIR / "stocker_demo.db"), help="ruta de salida")
    args = parser.parse_args(argv)

    tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else NEWS_PRIORITY_TICKERS
    export_demo_db(tickers, Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
