"""
tests/test_auditoria.py — pruebas de las correcciones de la auditoría del 16/09/2026.

No descargan nada ni tocan data/stocker.db: cada prueba usa una base de datos
SQLite temporal. Ejecutar desde la raíz del proyecto:

    python -m pytest tests -q
"""

from __future__ import annotations

import datetime as dt
import importlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Base de datos vacía en un directorio temporal, con los módulos
    recargados para que lean el nuevo STOCKER_DB_PATH."""
    monkeypatch.setenv("STOCKER_DB_PATH", str(tmp_path / "test.db"))
    import config

    importlib.reload(config)
    import db

    importlib.reload(db)
    engine = db.init_db()
    db.seed_stocks(engine, tickers=["AAA", "BBB"])
    return engine


# Martes 15/09/2026 a las 18:00 en Madrid (16:00 UTC): la sesión del 15 está abierta.
DURING_SESSION = pd.Timestamp("2026-09-15 16:00", tz="UTC")
# Mismo día a las 23:00 en Madrid (21:00 UTC): la sesión del 15 ya cerró.
AFTER_CLOSE = pd.Timestamp("2026-09-15 21:00", tz="UTC")


def test_last_closed_session():
    import market_time

    assert market_time.last_closed_session(DURING_SESSION) == dt.date(2026, 9, 14)
    assert market_time.last_closed_session(AFTER_CLOSE) == dt.date(2026, 9, 15)
    assert market_time.market_is_open(DURING_SESSION)
    assert not market_time.market_is_open(AFTER_CLOSE)


def test_drop_unclosed_sessions():
    import load

    df = pd.DataFrame({"date": [dt.date(2026, 9, 11), dt.date(2026, 9, 14), dt.date(2026, 9, 15)]})
    assert list(load.drop_unclosed_sessions(df, "AAA", now=DURING_SESSION)["date"]) == df["date"][:2].tolist()
    assert len(load.drop_unclosed_sessions(df, "AAA", now=AFTER_CLOSE)) == 3


def _prices(dates, closes, adj=None):
    return pd.DataFrame({
        "ticker": "AAA", "date": dates, "open": closes, "high": closes, "low": closes,
        "close": closes, "adj_close": adj if adj is not None else closes, "volume": 1000,
    })


def test_corporate_action_detects_split(tmp_db):
    import load

    dates = [dt.date(2026, 8, d) for d in (24, 25, 26)]
    load.upsert_daily_prices(_prices(dates, [158.0, 160.0, 161.0]), tmp_db)
    # yfinance tras un split 2x1: las mismas fechas, a mitad de precio.
    cleaned = _prices(dates, [79.0, 80.0, 80.5])
    raw = pd.DataFrame({"Stock Splits": [0, 0, 0], "Dividends": [0, 0, 0]})
    assert "close" in load.corporate_action_reason(raw, cleaned, "AAA", tmp_db)
    # Si coincide con lo guardado, no hay recarga.
    assert load.corporate_action_reason(raw, _prices(dates, [158.0, 160.0, 161.0]), "AAA", tmp_db) is None


def test_resolve_uses_date_origen_and_skips_open_session():
    import track_predictions as tp

    closes = pd.DataFrame({
        "ticker": "AAA",
        "date": [dt.date(2026, 9, d) for d in (9, 10, 11, 14, 15)],
        "close": [100.0, 90.0, 95.0, 101.0, 99.0],
    })
    pending = pd.DataFrame({
        "ticker": ["AAA", "AAA"],
        "date_predicha": [dt.date(2026, 9, 14), dt.date(2026, 9, 15)],
        "model_version": ["random_forest_h1_20260909170008"] * 2,
        # Origen explícito del 9/9 (no la fila anterior): 101 > 100 -> sube.
        "date_origen": [dt.date(2026, 9, 9), dt.date(2026, 9, 14)],
    })
    during = tp.resolve_predictions(pending, closes, now=DURING_SESSION)
    assert list(during["date_predicha"]) == [dt.date(2026, 9, 14)]
    assert during["actual_target_up_down"].tolist() == [1]
    after = tp.resolve_predictions(pending, closes, now=AFTER_CLOSE)
    assert after["actual_target_up_down"].tolist() == [1, 0]


def test_upsert_predictions_keeps_resolved_rows(tmp_db):
    import db
    import predict

    base = {
        "ticker": "AAA", "date_predicha": dt.date(2026, 9, 15), "date_origen": dt.date(2026, 9, 14),
        "model_version": "random_forest_h1_x", "predicted_target_up_down": 1,
        "predicted_probability": 0.6, "predicted_at": dt.datetime(2026, 9, 14, 23),
    }
    predict.upsert_predictions(pd.DataFrame([base]), tmp_db)
    with tmp_db.begin() as conn:
        conn.execute(db.predictions.update().values(actual_target_up_down=0))
    predict.upsert_predictions(pd.DataFrame([{**base, "predicted_target_up_down": 0, "predicted_probability": 0.4}]), tmp_db)
    with tmp_db.begin() as conn:
        row = conn.execute(db.predictions.select()).one()
    assert row.actual_target_up_down == 0 and row.predicted_target_up_down == 1


def test_persistence_baseline_matches_horizon():
    import model

    df = pd.DataFrame({"return_1d": [0.01, -0.01], "return_5d": [-0.02, 0.03], "return_20d": [0.05, -0.04]})
    assert model.persistence_baseline(df, 1).tolist() == [1, 0]
    assert model.persistence_baseline(df, 5).tolist() == [0, 1]
    assert model.persistence_baseline(df, 20).tolist() == [1, 0]


def test_plan_daily_update_prioritises_oldest_and_caps_window():
    import news

    today = dt.date(2026, 9, 16)
    tickers = news.TICKERS
    last = {t: today - dt.timedelta(days=1) for t in tickers}
    stale = tickers[-1]  # un ticker no prioritario sin consultar desde hace 90 días
    last[stale] = today - dt.timedelta(days=90)
    plan = news.plan_daily_update(last, today, max_calls=25)
    assert len(plan) == 25
    entry = next(p for p in plan if p[0] == stale)
    assert entry[1] == today - dt.timedelta(days=news.NEWS_DAILY_MAX_WINDOW_DAYS)
    assert entry[2] == today + dt.timedelta(days=1)


def test_mask_secrets():
    import news

    msg = "We have detected your API key as ABCD1234XYZ and our standard API rate limit is 25 requests per day."
    assert "ABCD1234XYZ" not in news.mask_secrets(msg)


def test_gold_target_uses_adj_close():
    import gold

    n = 60
    dates = pd.bdate_range("2026-01-01", periods=n).date
    close = np.linspace(100, 110, n)
    adj = close.copy()
    adj[46] = adj[45] * 1.01  # close cae (ex-dividendo), adj_close sube
    close[46] = close[45] * 0.99
    raw = pd.DataFrame({"date": dates, "open": close, "high": close, "low": close,
                        "close": close, "adj_close": adj, "volume": 1000})
    train, _ = gold.build_gold_frames(raw, "AAA")
    row = train[train["date"] == dates[45]]
    assert row["target_up_down"].iloc[0] == 1
