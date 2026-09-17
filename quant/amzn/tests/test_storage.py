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


def _save_row(conn, date, config, last_price=100.0, per=20.0, pbr=5.0):
    storage.save_price_daily(conn, {
        "date": date, "symbol": "AMZN", "last_price": last_price,
        "per": per, "pbr": pbr, "eps": None, "bps": None,
        "market_cap": None, "shares_outstanding": None,
        "volume": None, "currency": "USD",
        "config_hash": config.get_hash(), "fetched_at": f"{date}T09:00:00",
    })


def test_price_history_returns_rows_oldest_first(tmp_path):
    db_path = tmp_path / "test_amzn.db"
    storage.init_db(db_path)
    config = AmznConfig()

    with sqlite3.connect(db_path) as conn:
        _save_row(conn, "20260909", config, last_price=101.0)
        _save_row(conn, "20260907", config, last_price=100.0)
        _save_row(conn, "20260908", config, last_price=102.0)
        conn.commit()
        history = storage.price_history(conn, "AMZN")

    assert [h["date"] for h in history] == ["20260907", "20260908", "20260909"]
    assert history[0]["last_price"] == 100.0


def test_price_history_empty_for_unknown_symbol(tmp_path):
    db_path = tmp_path / "test_amzn.db"
    storage.init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        assert storage.price_history(conn, "AMZN") == []


def test_latest_price_row_returns_max_date(tmp_path):
    db_path = tmp_path / "test_amzn.db"
    storage.init_db(db_path)
    config = AmznConfig()

    with sqlite3.connect(db_path) as conn:
        _save_row(conn, "20260907", config, last_price=100.0)
        _save_row(conn, "20260909", config, last_price=102.0)
        conn.commit()
        latest = storage.latest_price_row(conn, "AMZN")

    assert latest["date"] == "20260909"
    assert latest["last_price"] == 102.0


def test_latest_price_row_none_when_empty(tmp_path):
    db_path = tmp_path / "test_amzn.db"
    storage.init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        assert storage.latest_price_row(conn, "AMZN") is None
