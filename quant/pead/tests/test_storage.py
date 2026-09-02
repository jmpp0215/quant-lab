import sqlite3

from quant.pead import storage
from quant.pead.config import PeadConfig


def _connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_save_surprise_round_trip(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    storage.init_db()

    config = PeadConfig(lookback_quarters=4, earnings_metric="operating_income", dart_basis="CFS")
    storage.save_surprise("005930", "20230515", config, 2.449, True)

    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM pead_surprises").fetchone()

    assert row["symbol"] == "005930"
    assert row["rcept_dt"] == "20230515"
    assert row["metric"] == "operating_income"
    assert row["basis"] == "CFS"
    assert row["config_hash"] == config.get_hash()
    assert row["surprise_score"] == 2.449
    assert row["is_estimable"] == 1


def test_save_surprise_not_estimable_stores_null_score(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    storage.init_db()

    config = PeadConfig(lookback_quarters=4, earnings_metric="operating_income", dart_basis="CFS")
    storage.save_surprise("005930", "20230515", config, None, False)

    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM pead_surprises").fetchone()

    assert row["surprise_score"] is None
    assert row["is_estimable"] == 0


def test_save_surprise_keys_by_config_hash():
    # 같은 (symbol, rcept_dt, metric, basis)라도 lookback_quarters가 다른 config는
    # surprise_score가 달라질 수 있으므로 별도 행으로 저장되어야 한다.
    config_a = PeadConfig(lookback_quarters=4, earnings_metric="operating_income", dart_basis="CFS")
    config_b = PeadConfig(lookback_quarters=8, earnings_metric="operating_income", dart_basis="CFS")
    assert config_a.get_hash() != config_b.get_hash()
