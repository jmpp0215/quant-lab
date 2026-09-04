import sqlite3
from decimal import Decimal

from quant.rebalance import Position
from quant.stock_factors import portfolio_construction, value
from quant.stock_factors.portfolio_construction import (
    PBRConfig,
    calculate_diff,
    price_data_is_stale,
)


def test_calculate_diff():
    target_weights = {
        "A": Decimal("0.5"),
        "B": Decimal("0.5"),
    }
    
    positions = {
        "A": Position(symbol="A", name="Stock A", quantity=10, last_price=Decimal("1000")),
        "C": Position(symbol="C", name="Stock C", quantity=5, last_price=Decimal("2000")),
    }
    
    prices = {
        "A": Decimal("1000"),
        "B": Decimal("2000"),
        "C": Decimal("2000"),
    }
    
    cash = Decimal("10000")
    
    # Total Assets = Cash(10,000) + A(10,000) + C(10,000) = 30,000
    # Target: A 15,000 (15 qty), B 15,000 (7.5 -> 7 qty), C 0 (0 qty)
    # A Delta: 15 - 10 = 5 BUY
    # B Delta: 7 - 0 = 7 BUY
    # C Delta: 0 - 5 = -5 SELL
    # Let's adjust prices to be above MIN_ORDER_KRW (50,000)
    
    prices = {
        "A": Decimal("10000"),
        "B": Decimal("10000"),
        "C": Decimal("10000"),
    }
    positions = {
        "A": Position(symbol="A", name="Stock A", quantity=10, last_price=Decimal("10000")),
        "C": Position(symbol="C", name="Stock C", quantity=10, last_price=Decimal("10000")),
    }
    cash = Decimal("100000")
    # Total Assets = 100k + 100k + 100k = 300,000
    # Target: A 150k (15 qty), B 150k (15 qty), C 0
    # A Delta: 15 - 10 = +5
    # B Delta: 15 - 0 = +15
    # C Delta: 0 - 10 = -10
    
    orders = calculate_diff(target_weights, positions, prices, cash)
    
    # Sells should come first
    assert len(orders) == 3
    assert orders[0].symbol == "C"
    assert orders[0].side == "SELL"
    assert orders[0].quantity == 10
    
    assert orders[1].symbol == "A"
    assert orders[1].side == "BUY"
    assert orders[1].quantity == 5
    
    assert orders[2].symbol == "B"
    assert orders[2].side == "BUY"
    assert orders[2].quantity == 15


class TestPriceDataIsStale:
    def test_none_latest_is_stale(self):
        assert price_data_is_stale("2026-09-04", None) is True

    def test_within_threshold_is_not_stale(self):
        # Monday -> Wednesday is a 2-trading-day gap, exactly at the default
        # threshold (2), so this should NOT be flagged as stale.
        assert price_data_is_stale("2026-01-07", "20260105") is False

    def test_beyond_threshold_is_stale(self):
        # Monday -> Thursday is a 3-trading-day gap, past the threshold.
        assert price_data_is_stale("2026-01-08", "20260105") is True

    def test_weekend_gap_is_not_stale(self):
        # Cached Friday, run Monday: 3 calendar days but only 1 trading day -
        # must not false-positive on the weekend.
        assert price_data_is_stale("2026-01-05", "20260102") is False

    def test_custom_threshold(self):
        assert price_data_is_stale("2026-01-08", "20260105", max_trading_days=5) is False


def _create_minimal_pbr_schema(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE pead_price_raw (
            date TEXT NOT NULL, symbol TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL,
            volume REAL, trading_value REAL,
            PRIMARY KEY (date, symbol)
        )
    """)
    conn.execute("""
        CREATE TABLE pead_quarterly_normalized (
            symbol TEXT NOT NULL, target_year TEXT NOT NULL, rcept_dt TEXT NOT NULL,
            report_code TEXT NOT NULL, metric TEXT NOT NULL, basis TEXT NOT NULL,
            quarterly_value REAL, is_estimable INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (symbol, target_year, rcept_dt, report_code, metric, basis)
        )
    """)
    conn.commit()
    conn.close()


def test_construct_target_portfolio_sees_current_year_prices(tmp_path, monkeypatch):
    """Regression test for the date-format bug: construct_target_portfolio's
    price query used to compare a dashed as_of_date ("2026-09-04") against
    undashed stored dates ("20260904"), which lexically excludes every date
    in as_of_date's own year - silently starving the signal of current data.
    """
    db_path = tmp_path / "test.db"
    _create_minimal_pbr_schema(db_path)
    monkeypatch.setattr(portfolio_construction, "DB_PATH", db_path)
    monkeypatch.setattr(value, "DB_PATH", db_path)

    as_of = "20260904"
    n_symbols = 60
    conn = sqlite3.connect(db_path)
    for i in range(n_symbols):
        symbol = f"{i:06d}"
        price = 1000 + i * 10  # ascending price -> ascending PBR (BPS fixed)
        conn.execute(
            "INSERT INTO pead_price_raw (date, symbol, close) VALUES (?, ?, ?)",
            (as_of, symbol, price),
        )
        for metric, val in (("total_equity", 1_000_000_000), ("issued_shares", 1_000_000)):
            conn.execute(
                """INSERT INTO pead_quarterly_normalized
                   (symbol, target_year, rcept_dt, report_code, metric, basis, quarterly_value)
                   VALUES (?, '2025', '20260101', '11013', ?, 'CFS', ?)""",
                (symbol, metric, val),
            )
    conn.commit()
    conn.close()

    weights = portfolio_construction.construct_target_portfolio(
        "2026-09-04", PBRConfig(target_n_stocks=10)
    )

    assert len(weights) == 10
    # BPS is identical across symbols, so lowest price == lowest PBR.
    assert set(weights) == {f"{i:06d}" for i in range(10)}
    assert all(w == Decimal("1") / Decimal(10) for w in weights.values())
