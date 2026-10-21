import sqlite3
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from quant.rebalance import Order, Position
from quant.stock_factors import portfolio_construction, value
from quant.stock_factors.portfolio_construction import (
    PBRConfig,
    calculate_diff,
    fit_buys_to_budget,
    halted_symbols,
    inverse_vol_weights,
    price_data_is_stale,
    resolve_target_quantities,
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


class TestResolveTargetQuantities:
    def test_rounds_to_nearest_not_floor_when_closer(self):
        # target 200,000 / price 110,000 = 1.818 -> nearest is 2, not floor's 1.
        qty = resolve_target_quantities(
            target_weights={"A": Decimal("1")},
            symbols={"A"},
            held_qty={},
            prices={"A": Decimal("110000")},
            total=Decimal("200000"),
            cash=Decimal("1000000"),
        )
        assert qty["A"] == 2

    def test_nearest_matches_floor_when_floor_is_already_closest(self):
        # target 200,000 / price 150,000 = 1.333 -> nearest is still 1, same as floor.
        qty = resolve_target_quantities(
            target_weights={"A": Decimal("1")},
            symbols={"A"},
            held_qty={},
            prices={"A": Decimal("150000")},
            total=Decimal("200000"),
            cash=Decimal("1000000"),
        )
        assert qty["A"] == 1

    def test_budget_overflow_trims_cheapest_bonus_first(self):
        # Both A (price 110,000, ideal 1.818) and B (price 125,000, ideal 1.6)
        # round up by one bonus share (floor 1 -> nearest 2 for both, target
        # 200,000 each). Total nearest-rounded buy cost =
        # 2*110,000 + 2*125,000 = 470,000, but only 400,000 cash is
        # available. Reverting just the cheaper bonus (A, -110,000 ->
        # 360,000) is enough to fit - B should keep its full nearest
        # quantity.
        qty = resolve_target_quantities(
            target_weights={"A": Decimal("0.5"), "B": Decimal("0.5")},
            symbols={"A", "B"},
            held_qty={},
            prices={"A": Decimal("110000"), "B": Decimal("125000")},
            total=Decimal("400000"),
            cash=Decimal("400000"),
        )
        assert qty["A"] == 1  # bonus reverted to floor
        assert qty["B"] == 2  # bonus kept

    def test_never_trims_below_floor_even_when_still_over_budget(self):
        # cash is far below even the floor-only total (2 * 110,000 =
        # 220,000) - trimming both bonuses still leaves the floor sum
        # (2 * 1 * 110,000 = 220,000) over the 50,000 cash. Quantities must
        # not go below floor (1 each), not drop to 0.
        qty = resolve_target_quantities(
            target_weights={"A": Decimal("0.5"), "B": Decimal("0.5")},
            symbols={"A", "B"},
            held_qty={},
            prices={"A": Decimal("110000"), "B": Decimal("110000")},
            total=Decimal("400000"),
            cash=Decimal("50000"),
        )
        assert qty["A"] == 1
        assert qty["B"] == 1

    def test_budget_cap_does_not_touch_sell_side_quantities(self):
        # C is being sold down toward a smaller target weight - its
        # nearest-rounded target must be unaffected by a buy-side budget
        # shortfall elsewhere.
        qty = resolve_target_quantities(
            target_weights={"A": Decimal("0.5"), "C": Decimal("0")},
            symbols={"A", "C"},
            held_qty={"C": 10},
            prices={"A": Decimal("110000"), "C": Decimal("10000")},
            total=Decimal("300000"),
            cash=Decimal("0"),
        )
        assert qty["C"] == 0


class TestPBRConfigWeightingScheme:
    def test_defaults_to_equal_weight(self):
        assert PBRConfig().weighting_scheme == "equal"

    def test_accepts_inverse_vol(self):
        assert PBRConfig(weighting_scheme="inverse_vol").weighting_scheme == "inverse_vol"

    def test_rejects_unknown_scheme(self):
        with pytest.raises(ValueError):
            PBRConfig(weighting_scheme="bogus")


def _synthetic_price_series(n_days: int, daily_vol: float, final_price: float, seed: int):
    """A price path with the given daily-return volatility, scaled so the
    last observation is exactly final_price."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0, daily_vol, n_days)
    path = np.cumprod(1 + rets)
    return final_price * path / path[-1]


class TestInverseVolWeights:
    def test_favors_lower_volatility_symbol(self):
        dates = pd.bdate_range("2025-01-01", periods=150)
        df = pd.DataFrame({
            "LOW": _synthetic_price_series(150, 0.002, 100, seed=1),
            "HIGH": _synthetic_price_series(150, 0.05, 100, seed=2),
        }, index=dates)

        weights = inverse_vol_weights(["LOW", "HIGH"], df, dates[-1].strftime("%Y%m%d"))

        assert weights is not None
        assert weights["LOW"] > weights["HIGH"]

    def test_weights_sum_to_approximately_one(self):
        dates = pd.bdate_range("2025-01-01", periods=150)
        symbols = [f"S{i}" for i in range(5)]
        df = pd.DataFrame({
            sym: _synthetic_price_series(150, 0.01 * (i + 1), 100, seed=i)
            for i, sym in enumerate(symbols)
        }, index=dates)

        weights = inverse_vol_weights(symbols, df, dates[-1].strftime("%Y%m%d"))

        assert weights is not None
        assert set(weights) == set(symbols)
        assert abs(sum(weights.values()) - Decimal("1")) < Decimal("0.001")

    def test_none_when_a_symbol_has_insufficient_history(self):
        # MIN_VOL_OBS is 30 - a symbol with only 10 days of real prices
        # (the rest NaN, as if recently listed) must force the whole
        # basket to fall back to equal weight, not just that one symbol.
        dates = pd.bdate_range("2025-01-01", periods=150)
        df = pd.DataFrame({
            "OLD": _synthetic_price_series(150, 0.01, 100, seed=1),
            "NEW": _synthetic_price_series(150, 0.01, 100, seed=2),
        }, index=dates)
        df.loc[df.index[:-10], "NEW"] = np.nan

        weights = inverse_vol_weights(["OLD", "NEW"], df, dates[-1].strftime("%Y%m%d"))

        assert weights is None

    def test_point_in_time_ignores_prices_after_target_date(self):
        # A huge volatility spike AFTER the target date must not affect the
        # weights computed as of the target date.
        dates = pd.bdate_range("2025-01-01", periods=160)
        target_date = dates[139]
        base = pd.DataFrame({
            "A": _synthetic_price_series(160, 0.01, 100, seed=1),
            "B": _synthetic_price_series(160, 0.01, 100, seed=2),
        }, index=dates)
        weights_before_spike = inverse_vol_weights(
            ["A", "B"], base, target_date.strftime("%Y%m%d"))

        spiked = base.copy()
        spiked.loc[spiked.index > target_date, "B"] *= 3  # future spike

        weights_with_future_spike = inverse_vol_weights(
            ["A", "B"], spiked, target_date.strftime("%Y%m%d"))

        assert weights_before_spike == weights_with_future_spike

    def test_corporate_action_jump_does_not_inflate_volatility(self):
        # 2026-10-06: 007460/003060 carried a +1,446%/+1,286% one-day
        # "return" in pead_price_raw (a 15:1 consolidation landing on
        # unadjusted history). Counted as volatility, it cut their weight to
        # ~0.04%. Two otherwise identical symbols must get equal weight
        # when one of them has such a re-basing step in its window.
        dates = pd.bdate_range("2025-01-01", periods=150)
        path = _synthetic_price_series(150, 0.01, 100, seed=1)
        jumped = path.copy()
        jumped[100:] *= 15  # consolidation: price re-based x15 overnight
        df = pd.DataFrame({"PLAIN": path, "JUMP": jumped}, index=dates)

        weights = inverse_vol_weights(["PLAIN", "JUMP"], df, dates[-1].strftime("%Y%m%d"))

        assert weights is not None
        assert abs(weights["PLAIN"] - weights["JUMP"]) < Decimal("0.01")

    def test_real_limit_moves_still_count_as_volatility(self):
        # KRX daily limit is ±30%: a symbol that genuinely moves that much
        # is volatile and must still be weighted down. Only moves beyond
        # price_guard's threshold (50%) are treated as artifacts.
        dates = pd.bdate_range("2025-01-01", periods=150)
        calm = _synthetic_price_series(150, 0.01, 100, seed=1)
        wild = calm.copy()
        for i in range(10, 150, 10):
            wild[i:] *= 1.3 if (i // 10) % 2 else 1 / 1.3
        df = pd.DataFrame({"CALM": calm, "WILD": wild}, index=dates)

        weights = inverse_vol_weights(["CALM", "WILD"], df, dates[-1].strftime("%Y%m%d"))

        assert weights["CALM"] > weights["WILD"] * 2

    def test_artifact_exclusion_can_trigger_the_short_history_fallback(self):
        # Dropping artifact days must not let a symbol slip under
        # MIN_VOL_OBS without the whole-basket equal-weight fallback.
        dates = pd.bdate_range("2025-01-01", periods=150)
        df = pd.DataFrame({
            "OLD": _synthetic_price_series(150, 0.01, 100, seed=1),
            "NEW": _synthetic_price_series(150, 0.01, 100, seed=2),
        }, index=dates)
        df.loc[df.index[:-31], "NEW"] = np.nan   # exactly MIN_VOL_OBS returns...
        df.loc[df.index[-15]:, "NEW"] *= 10        # ...one of them an artifact

        weights = inverse_vol_weights(["OLD", "NEW"], df, dates[-1].strftime("%Y%m%d"))

        assert weights is None


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


def _insert_linear_universe(db_path, as_of, n_symbols, equity_of=lambda i: 1_000_000_000):
    """n_symbols names priced 1000 + 10i, 1M shares each (BPS = equity / 1M)."""
    conn = sqlite3.connect(db_path)
    for i in range(n_symbols):
        symbol = f"{i:06d}"
        conn.execute(
            "INSERT INTO pead_price_raw (date, symbol, close) VALUES (?, ?, ?)",
            (as_of, symbol, 1000 + i * 10),
        )
        for metric, val in (("total_equity", equity_of(i)), ("issued_shares", 1_000_000)):
            conn.execute(
                """INSERT INTO pead_quarterly_normalized
                   (symbol, target_year, rcept_dt, report_code, metric, basis, quarterly_value)
                   VALUES (?, '2025', '20260101', '11013', ?, 'CFS', ?)""",
                (symbol, metric, val),
            )
    conn.commit()
    conn.close()


class TestRankBoundary:
    def test_boundary_is_bps_times_rank_n_plus_1_pbr(self, tmp_path, monkeypatch):
        """BPS 1000 everywhere except symbol 0 (2000); rank 11 is symbol 10
        at 1100, PBR 1.1 - so the boundary is 1100 for symbols 1-9 and
        2200 for symbol 0 (twice the book value, still PBR 0.5 at 1000)."""
        db_path = tmp_path / "test.db"
        _create_minimal_pbr_schema(db_path)
        monkeypatch.setattr(portfolio_construction, "DB_PATH", db_path)
        monkeypatch.setattr(value, "DB_PATH", db_path)
        _insert_linear_universe(db_path, "20260904", 60,
                                equity_of=lambda i: 2_000_000_000 if i == 0 else 1_000_000_000)

        config = PBRConfig(target_n_stocks=10)
        weights, boundary = portfolio_construction.construct_target_portfolio_with_boundary(
            "2026-09-04", config)

        assert set(boundary) == set(weights)
        assert boundary["000000"] == Decimal("2200")
        assert all(boundary[f"{i:06d}"] == Decimal("1100") for i in range(1, 10))
        assert weights == portfolio_construction.construct_target_portfolio("2026-09-04", config)

    def test_empty_without_a_rank_n_plus_1(self, tmp_path, monkeypatch):
        db_path = tmp_path / "test.db"
        _create_minimal_pbr_schema(db_path)
        monkeypatch.setattr(portfolio_construction, "DB_PATH", db_path)
        monkeypatch.setattr(value, "DB_PATH", db_path)
        _insert_linear_universe(db_path, "20260904", 60)  # bottom 20% = 12 names

        weights, boundary = portfolio_construction.construct_target_portfolio_with_boundary(
            "2026-09-04", PBRConfig(target_n_stocks=50))

        assert len(weights) == 12
        assert boundary == {}


def test_construct_target_portfolio_wires_up_inverse_vol_weighting(tmp_path, monkeypatch):
    """End-to-end: PBRConfig(weighting_scheme="inverse_vol") should select the
    same symbols as equal weight (selection and sizing are independent) but
    size them differently, using construct_target_portfolio's own
    df_price_pivot rather than a synthetic one - this is what actually
    exercises the wiring, not just inverse_vol_weights() in isolation."""
    db_path = tmp_path / "test.db"
    _create_minimal_pbr_schema(db_path)
    monkeypatch.setattr(portfolio_construction, "DB_PATH", db_path)
    monkeypatch.setattr(value, "DB_PATH", db_path)

    n_symbols = 55  # get_pbr_signal_func requires >=50 valid candidates
    n_days = 130
    dates = pd.bdate_range(end="2025-06-27", periods=n_days)
    as_of = dates[-1].strftime("%Y%m%d")

    conn = sqlite3.connect(db_path)
    for i in range(n_symbols):
        symbol = f"{i:06d}"
        # Alternating low/high volatility gives inverse-vol weighting
        # something to differentiate; every path is scaled to end at the
        # same final price used by the equal-weight test above, so ranking
        # (and therefore which symbols get selected) is unaffected by which
        # weighting scheme is asked for.
        daily_vol = 0.003 if i % 2 == 0 else 0.03
        final_price = 1000 + i * 10
        prices = _synthetic_price_series(n_days, daily_vol, final_price, seed=i)
        for d, price in zip(dates, prices, strict=True):
            conn.execute(
                "INSERT INTO pead_price_raw (date, symbol, close) VALUES (?, ?, ?)",
                (d.strftime("%Y%m%d"), symbol, float(price)),
            )
        for metric, val in (("total_equity", 1_000_000_000), ("issued_shares", 1_000_000)):
            conn.execute(
                """INSERT INTO pead_quarterly_normalized
                   (symbol, target_year, rcept_dt, report_code, metric, basis, quarterly_value)
                   VALUES (?, '2025', '20250101', '11013', ?, 'CFS', ?)""",
                (symbol, metric, val),
            )
    conn.commit()
    conn.close()

    equal_weights = portfolio_construction.construct_target_portfolio(
        as_of, PBRConfig(target_n_stocks=10, weighting_scheme="equal"))
    inv_weights = portfolio_construction.construct_target_portfolio(
        as_of, PBRConfig(target_n_stocks=10, weighting_scheme="inverse_vol"))

    assert set(equal_weights) == set(inv_weights)
    assert inv_weights != equal_weights
    assert abs(sum(inv_weights.values()) - Decimal("1")) < Decimal("0.001")


def test_construct_target_portfolio_falls_back_to_equal_on_thin_history(tmp_path, monkeypatch):
    """Only a handful of days of price history (well under MIN_VOL_OBS) -
    inverse_vol should fall back to equal weight rather than erroring or
    producing a degenerate weighting."""
    db_path = tmp_path / "test.db"
    _create_minimal_pbr_schema(db_path)
    monkeypatch.setattr(portfolio_construction, "DB_PATH", db_path)
    monkeypatch.setattr(value, "DB_PATH", db_path)

    as_of = "20260904"
    n_symbols = 55  # get_pbr_signal_func requires >=50 valid candidates
    conn = sqlite3.connect(db_path)
    for i in range(n_symbols):
        symbol = f"{i:06d}"
        price = 1000 + i * 10
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
        as_of, PBRConfig(target_n_stocks=10, weighting_scheme="inverse_vol"))

    assert len(weights) == 10
    assert all(w == Decimal("1") / Decimal(10) for w in weights.values())


def _pos(symbol, qty, price="10000"):
    return Position(symbol=symbol, name=symbol, quantity=qty, last_price=Decimal(price))


class TestCalculateDiffLocked:
    """A halted holding (locked) can't be sold - no order for it, and its
    value must not fund buys, or the plan spends cash the account doesn't have."""

    @pytest.fixture(autouse=True)
    def _no_network_listing(self, monkeypatch):
        monkeypatch.setattr(portfolio_construction.fdr, "StockListing",
                            lambda _market: pd.DataFrame({"Code": [], "Name": []}))

    PRICES = {s: Decimal("10000") for s in ("A", "B", "H")}

    def test_locked_holding_gets_no_sell_and_is_excluded_from_budget(self):
        # A 100k + H(halted) 100k + cash 100k. Investable total = 200k, so
        # A stays at 10 and B buys 10 (100k) - exactly the cash on hand.
        # Unlocked, total would be 300k: A +5, B +15 = 200k of buys vs 100k cash.
        positions = {"A": _pos("A", 10), "H": _pos("H", 10)}
        weights = {"A": Decimal("0.5"), "B": Decimal("0.5")}
        cash = Decimal("100000")

        orders = calculate_diff(weights, positions, self.PRICES, cash, locked={"H"})

        assert [(o.symbol, o.side, o.quantity) for o in orders] == [("B", "BUY", 10)]
        assert sum(o.notional for o in orders if o.side == "BUY") <= cash

    def test_unlocked_plan_overspends_for_contrast(self):
        positions = {"A": _pos("A", 10), "H": _pos("H", 10)}
        weights = {"A": Decimal("0.5"), "B": Decimal("0.5")}
        cash = Decimal("100000")

        orders = calculate_diff(weights, positions, self.PRICES, cash)

        # Without the lock the H sell is planned (and would fail live), and
        # the buys assume its proceeds.
        assert ("H", "SELL", 10) in [(o.symbol, o.side, o.quantity) for o in orders]
        assert sum(o.notional for o in orders if o.side == "BUY") > cash

    def test_locked_name_in_target_is_dropped_and_rest_renormalized(self):
        # Retry replays a target that still names H. H is dropped, A's 0.5
        # renormalizes to 1.0 of the 200k investable total -> A to 20 shares.
        positions = {"A": _pos("A", 10), "H": _pos("H", 10)}
        weights = {"A": Decimal("0.5"), "H": Decimal("0.5")}

        orders = calculate_diff(weights, positions, self.PRICES, Decimal("100000"), locked={"H"})

        assert [(o.symbol, o.side, o.quantity) for o in orders] == [("A", "BUY", 10)]

    def test_locked_symbol_not_held_is_ignored(self):
        # Only holdings can be locked; a locked-but-unheld target is still bought.
        weights = {"B": Decimal("1")}

        orders = calculate_diff(weights, {}, self.PRICES, Decimal("100000"), locked={"B"})

        assert [(o.symbol, o.side, o.quantity) for o in orders] == [("B", "BUY", 10)]

    def test_empty_lock_matches_default(self):
        positions = {"A": _pos("A", 10), "C": _pos("C", 10)}
        weights = {"A": Decimal("0.5"), "B": Decimal("0.5")}
        prices = {**self.PRICES, "C": Decimal("10000")}
        cash = Decimal("100000")

        assert (calculate_diff(weights, positions, prices, cash, locked=set())
                == calculate_diff(weights, positions, prices, cash))


class TestCalculateDiffMinOrder:
    """Buys and sells have separate minimum order sizes: 10k for buys, 50k
    for sells. A single 50k floor (≈23% of a ~220k PBR position) skipped
    every small top-up after a falling market, leaving ~7% of the account
    idle in cash on 2026-09-14; sells keep 50k so small trims don't churn."""

    @pytest.fixture(autouse=True)
    def _no_network_listing(self, monkeypatch):
        monkeypatch.setattr(portfolio_construction.fdr, "StockListing",
                            lambda _market: pd.DataFrame({"Code": [], "Name": []}))

    def test_buy_40k_short_is_ordered(self):
        # A 120k + B 80k + cash 40k = 240k -> 120k each; B short by 40k.
        positions = {"A": _pos("A", 12), "B": _pos("B", 8)}
        weights = {"A": Decimal("0.5"), "B": Decimal("0.5")}
        prices = {"A": Decimal("10000"), "B": Decimal("10000")}

        orders = calculate_diff(weights, positions, prices, Decimal("40000"))

        assert [(o.symbol, o.side, o.quantity) for o in orders] == [("B", "BUY", 4)]

    def test_sell_40k_over_is_not_ordered(self):
        # A 160k + B 80k = 240k -> 120k each; A over by 40k, under the 50k sell floor.
        positions = {"A": _pos("A", 16), "B": _pos("B", 8)}
        weights = {"A": Decimal("0.5"), "B": Decimal("0.5")}
        prices = {"A": Decimal("10000"), "B": Decimal("10000")}

        orders = calculate_diff(weights, positions, prices, Decimal("0"))

        assert not [o for o in orders if o.side == "SELL"]

    def test_buy_under_10k_is_skipped(self):
        # A 100k + B 90k + cash 9k = 199k -> 99.5k each; B rounds up by one
        # 9,000 share, under the 10k buy floor.
        positions = {"A": _pos("A", 10), "B": _pos("B", 10, "9000")}
        weights = {"A": Decimal("0.5"), "B": Decimal("0.5")}
        prices = {"A": Decimal("10000"), "B": Decimal("9000")}

        orders = calculate_diff(weights, positions, prices, Decimal("9000"))

        assert orders == []

    def test_small_shortfalls_everywhere_deploy_idle_cash(self):
        # 2026-09-14 shape: 45 equal-weight names each held at ~90% of target
        # after a drawdown, ~10% of the account in cash. Every name is short
        # by ~22k - below the old single 50k floor, so nothing was bought.
        price_cycle = ["1500", "3000", "7000", "12000"]
        prices, positions = {}, {}
        for i in range(45):
            sym = f"S{i:02d}"
            price = Decimal(price_cycle[i % len(price_cycle)])
            prices[sym] = price
            positions[sym] = _pos(sym, int(Decimal("200000") / price), str(price))
        weights = {s: Decimal(1) / Decimal(45) for s in prices}
        cash = Decimal("1000000")
        total = cash + sum(p.value for p in positions.values())

        orders = calculate_diff(weights, positions, prices, cash)

        cash_left = (cash
                     + sum(o.quantity * prices[o.symbol] for o in orders if o.side == "SELL")
                     - sum(o.quantity * prices[o.symbol] for o in orders if o.side == "BUY"))
        assert cash_left >= 0
        assert cash_left / total < Decimal("0.01")


class TestCalculateDiffBuyBudget:
    """Buys may spend only cash + the sells actually placed. A trim under the
    50k sell floor is skipped, so its proceeds never arrive; on 2026-10-16
    the unscaled buys ran ~242k past the cash and Toss rejected the last
    ones with 422 insufficient-buying-power, aborting the run."""

    @pytest.fixture(autouse=True)
    def _no_network_listing(self, monkeypatch):
        monkeypatch.setattr(portfolio_construction.fdr, "StockListing",
                            lambda _market: pd.DataFrame({"Code": [], "Name": []}))

    def test_buys_scaled_proportionally_when_skipped_sell_leaves_shortfall(self):
        # A 140k (over by 40k, sell skipped), B 60k, C 0, cash 100k = 300k
        # -> 100k each. Buys planned: B +4 (40k), C +10 (100k) = 140k vs
        # 100k cash -> factor 0.714: B +2, C +7 = 90k. The 10k left goes to
        # C, furthest short (30k vs B's 20k): C +8, total 100k.
        positions = {"A": _pos("A", 14), "B": _pos("B", 6)}
        weights = {"A": Decimal("1") / 3, "B": Decimal("1") / 3, "C": Decimal("1") / 3}
        prices = {"A": Decimal("10000"), "B": Decimal("10000"), "C": Decimal("10000")}

        orders = calculate_diff(weights, positions, prices, Decimal("100000"))

        assert [(o.symbol, o.side, o.quantity) for o in orders] == [
            ("B", "BUY", 2), ("C", "BUY", 8)]

    def test_planned_sell_proceeds_count_toward_budget(self):
        # C sold for 100k funds the buys alongside 100k cash - no scaling.
        positions = {"A": _pos("A", 10), "C": _pos("C", 10)}
        weights = {"A": Decimal("0.5"), "B": Decimal("0.5")}
        prices = {"A": Decimal("10000"), "B": Decimal("10000"), "C": Decimal("10000")}

        orders = calculate_diff(weights, positions, prices, Decimal("100000"))

        assert [(o.symbol, o.quantity) for o in orders if o.side == "BUY"] == [
            ("A", 5), ("B", 15)]

    def test_scaled_buys_never_exceed_budget(self):
        prices = {"A": Decimal("3535"), "B": Decimal("47950"), "C": Decimal("1906"),
                  "D": Decimal("10000")}
        positions = {"D": _pos("D", 30)}  # over target by < 50k, sell skipped
        weights = {s: Decimal("0.25") for s in prices}
        cash = Decimal("870000")

        orders = calculate_diff(weights, positions, prices, cash)

        buys = [o for o in orders if o.side == "BUY"]
        assert buys
        assert sum(o.notional for o in buys) <= cash

    def test_scaled_buy_under_10k_is_dropped(self):
        # A 160k, B 80k, cash 60k = 300k -> A 120k (over 40k, sell skipped),
        # B 90k (+1), C 90k (+9). Buys 100k vs 60k -> factor 0.6: B 0, C 5,
        # 10k left goes to C (furthest short) -> C 6. B stays at 0, dropped.
        positions = {"A": _pos("A", 16), "B": _pos("B", 8)}
        weights = {"A": Decimal("0.4"), "B": Decimal("0.3"), "C": Decimal("0.3")}
        prices = {"A": Decimal("10000"), "B": Decimal("10000"), "C": Decimal("10000")}

        orders = calculate_diff(weights, positions, prices, Decimal("60000"))

        assert [(o.symbol, o.side, o.quantity) for o in orders] == [("C", "BUY", 6)]


def _buy(symbol, qty, price):
    return Order(symbol=symbol, name=symbol, side="BUY", quantity=qty,
                 limit_price=Decimal(price))


class TestFitBuysToBudget:
    MIN = Decimal("10000")

    def test_within_budget_is_unchanged(self):
        buys = [_buy("A", 5, "10000")]
        assert fit_buys_to_budget(buys, Decimal("50000"), self.MIN) == buys

    def test_leftover_buys_back_a_floored_expensive_share(self):
        # 2026-10-16 shape: four 3 x 9,000 buys plus one 47,950 share,
        # 155,950 planned vs 150,000. Factor 0.962 floors each Y to 2
        # (72k) and X to 0; the 78k left goes to X first (furthest short,
        # 47,950), then one share each to three Ys (27k): 146,950.
        buys = [_buy("X", 1, "47950")] + [_buy(f"Y{i}", 3, "9000") for i in range(4)]

        fitted = {o.symbol: o.quantity for o in fit_buys_to_budget(buys, Decimal("150000"), self.MIN)}

        assert fitted["X"] == 1
        assert sorted(fitted[f"Y{i}"] for i in range(4)) == [2, 3, 3, 3]

    def test_never_exceeds_budget_or_planned_quantity(self):
        buys = [_buy("A", 7, "3535"), _buy("B", 2, "47950"), _buy("C", 40, "1906"),
                _buy("D", 3, "33950")]
        budget = Decimal("200000")

        fitted = fit_buys_to_budget(buys, budget, self.MIN)

        planned = {o.symbol: o.quantity for o in buys}
        assert sum(o.notional for o in fitted) <= budget
        assert all(o.quantity <= planned[o.symbol] for o in fitted)

    def test_buy_left_under_min_order_is_dropped(self):
        # A planned 1 x 5,000 floors to 0; leftover goes to B first (larger
        # shortfall) and nothing is left for A, so A drops out entirely.
        buys = [_buy("A", 1, "5000"), _buy("B", 10, "10000")]

        fitted = fit_buys_to_budget(buys, Decimal("60000"), self.MIN)

        assert [(o.symbol, o.quantity) for o in fitted] == [("B", 6)]

    def test_zero_budget_buys_nothing(self):
        assert fit_buys_to_budget([_buy("A", 5, "10000")], Decimal("0"), self.MIN) == []


class TestHaltedSymbols:
    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        db_path = tmp_path / "test.db"
        _create_minimal_pbr_schema(db_path)
        monkeypatch.setattr(portfolio_construction, "DB_PATH", db_path)
        conn = sqlite3.connect(db_path)
        rows = [
            # 20260903: B had a zero-volume day earlier but traded on the latest date
            ("20260903", "A", 0), ("20260903", "B", 0), ("20260903", "C", 100),
            ("20260904", "A", 0), ("20260904", "B", 500), ("20260904", "C", 100),
            ("20260907", "A", 900), ("20260907", "B", 0), ("20260907", "C", 100),
        ]
        conn.executemany(
            "INSERT INTO pead_price_raw (date, symbol, close, volume) VALUES (?, ?, 1000, ?)", rows)
        conn.commit()
        conn.close()

    def test_zero_volume_on_latest_date_at_or_before_as_of(self, db):
        assert halted_symbols({"A", "B", "C"}, "2026-09-04") == {"A"}

    def test_as_of_between_dates_uses_prior_close(self, db):
        # Saturday 09-05 -> latest cached date is Friday 09-04.
        assert halted_symbols({"A", "B", "C"}, "20260905") == {"A"}

    def test_only_requested_symbols_are_returned(self, db):
        assert halted_symbols({"C"}, "2026-09-04") == set()
        assert halted_symbols({"B", "C"}, "2026-09-07") == {"B"}

    def test_empty_input(self, db):
        assert halted_symbols(set(), "2026-09-07") == set()
