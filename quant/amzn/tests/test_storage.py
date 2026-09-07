import sqlite3

from quant.amzn import storage
from quant.amzn.config import AmznConfig


def test_init_db_creates_all_three_tables(tmp_path):
    db_path = tmp_path / "test_amzn.db"
    storage.init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    assert {"amzn_price_daily", "amzn_segment_quarterly", "amzn_news_events"} <= tables


def test_config_hash_is_stable_and_changes_with_fields():
    a = AmznConfig()
    b = AmznConfig()
    assert a.get_hash() == b.get_hash()

    c = AmznConfig(llm_model="different-model")
    assert c.get_hash() != a.get_hash()


def test_price_daily_round_trip(tmp_path):
    db_path = tmp_path / "test_amzn.db"
    storage.init_db(db_path)
    config = AmznConfig()

    with sqlite3.connect(db_path) as conn:
        storage.save_price_daily(conn, {
            "date": "20260907", "symbol": "AMZN", "last_price": 215.43,
            "per": 38.1, "pbr": 7.9, "eps": 5.65, "bps": 27.31,
            "market_cap": 2.2e12, "shares_outstanding": 1.05e10,
            "volume": 4.2e7, "currency": "USD",
            "config_hash": config.get_hash(), "fetched_at": "2026-09-07T09:00:00",
        })
        conn.commit()
        row = conn.execute(
            "SELECT last_price, per FROM amzn_price_daily WHERE date='20260907'"
        ).fetchone()

    assert row == (215.43, 38.1)
