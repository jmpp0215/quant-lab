"""Tests for backtest.py's dividend/cash accounting.

run_tranched() drives strategy.evaluate() for real, but that pulls in
config.UNIVERSE/CASH_SYMBOL/LOOKBACK_MONTHS and months of realistic
multi-symbol history to produce a ranking - none of which this module
needs to exercise, since what's under test here is purely the value/cash
bookkeeping around a given signal. strategy.evaluate is monkeypatched to
a fixed "always 100% symbol A" signal so the holding schedule is
deterministic, and prices are held flat so any change in value is
attributable to dividends alone, not price movement.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

import backtest
from quant import config, strategy


def candles(dates: list[date], price: str = "100") -> list[dict]:
    """Newest-first flat-price candles, matching tests/test_momentum.py's
    candle shape."""
    return [
        {"timestamp": f"{d.isoformat()}T00:00:00.000+09:00", "closePrice": price}
        for d in sorted(dates, reverse=True)
    ]


def events(rows: list[tuple[str, str]]) -> list[dict]:
    """Matches tests/test_momentum.py's dividend-event shape."""
    return [{"record_date": d, "amount": Decimal(a)} for d, a in rows]


def _weekdays(start: date, end: date) -> list[date]:
    out = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# ~20 months of weekday-only trading days, flat price - enough for
# _tranche_schedule()'s default skip_months=13 to leave several usable
# rebalance points, with TOP_N/LOOKBACK_MONTHS irrelevant since
# strategy.evaluate is monkeypatched below.
ALL_DATES = _weekdays(date(2023, 1, 1), date(2024, 8, 30))


@pytest.fixture(autouse=True)
def small_universe(monkeypatch):
    """Two symbols is enough - strategy.evaluate is replaced entirely, so
    config.UNIVERSE only needs to give _tranche_schedule() a reference
    symbol with candles."""
    monkeypatch.setattr(config, "UNIVERSE", {"A": "Stock A", "B": "Stock B"})


@pytest.fixture(autouse=True)
def fixed_signal_on_a(monkeypatch):
    """Always 100% A, regardless of candles/dividends handed in - makes the
    holding schedule deterministic so a flat price isolates the dividend
    contribution exactly."""
    def fake_evaluate(candles_by_symbol, dividend_events_by_symbol):
        return strategy.Signal(
            weights={"A": Decimal("1")}, cash_weight=Decimal("0"), scores=[])
    monkeypatch.setattr(strategy, "evaluate", fake_evaluate)


@pytest.fixture
def candles_by_symbol():
    return {
        "A": candles(ALL_DATES),
        "B": candles(ALL_DATES),
    }


class TestDividendIncome:
    def test_sums_units_times_amount(self):
        holdings = {"A": Decimal("100")}
        evs = {"A": events([("2024-03-01", "2")])}
        assert backtest.dividend_income(
            holdings, evs, "2024-02-01", "2024-04-01") == Decimal("200")

    def test_since_is_exclusive(self):
        # Already counted in the prior period - must not double-count.
        holdings = {"A": Decimal("100")}
        evs = {"A": events([("2024-02-01", "2")])}
        assert backtest.dividend_income(
            holdings, evs, "2024-02-01", "2024-04-01") == Decimal("0")

    def test_until_is_inclusive(self):
        holdings = {"A": Decimal("100")}
        evs = {"A": events([("2024-04-01", "2")])}
        assert backtest.dividend_income(
            holdings, evs, "2024-02-01", "2024-04-01") == Decimal("200")

    def test_skips_zero_unit_holdings(self):
        holdings = {"A": Decimal("0")}
        evs = {"A": events([("2024-03-01", "2")])}
        assert backtest.dividend_income(
            holdings, evs, "2024-02-01", "2024-04-01") == Decimal("0")

    def test_ignores_symbols_with_no_events(self):
        holdings = {"A": Decimal("100"), "B": Decimal("50")}
        evs = {"A": events([("2024-03-01", "2")])}  # no "B" key at all
        assert backtest.dividend_income(
            holdings, evs, "2024-02-01", "2024-04-01") == Decimal("200")


class TestRunTranchedDividends:
    def test_pools_dividend_income_into_cash(self, candles_by_symbol):
        schedule = backtest._tranche_schedule(candles_by_symbol, list(config.TRANCHES))
        assert len(schedule) >= 3, "fixture needs a seed date plus 2 loop iterations"

        # Strictly between the second and third schedule entries (i.e.
        # inside the first real loop iteration's window).
        second_date, third_date = (
            date.fromisoformat(schedule[1][0]),
            date.fromisoformat(schedule[2][0]),
        )
        mid = second_date + timedelta(days=1)
        while mid >= third_date or mid.weekday() >= 5:
            mid -= timedelta(days=1)
        assert second_date < mid < third_date

        divs = {"A": events([(mid.isoformat(), "2")]), "B": []}
        history = backtest.run_tranched(candles_by_symbol, divs)

        # All sleeves are seeded 100% into A at price 100 -> initial/100
        # units total, same as the single-book run() case.
        units_held = Decimal("10000000") / Decimal("100")
        before = next(r for r in history if r.date == schedule[1][0])
        after = next(r for r in history if r.date == schedule[2][0])
        assert after.value == before.value + units_held * Decimal("2")

    def test_includes_dividend_income_in_final_valuation(self, candles_by_symbol):
        schedule = backtest._tranche_schedule(candles_by_symbol, list(config.TRANCHES))
        last_date = date.fromisoformat(schedule[-1][0])
        after_last = last_date + timedelta(days=1)
        while after_last.weekday() >= 5:
            after_last += timedelta(days=1)

        divs = {"A": events([(after_last.isoformat(), "3")]), "B": []}
        history = backtest.run_tranched(candles_by_symbol, divs)

        units_held = Decimal("10000000") / Decimal("100")
        second_to_last, final = history[-2], history[-1]
        assert final.value == second_to_last.value + units_held * Decimal("3")

    def test_matches_no_dividend_when_no_events(self, candles_by_symbol):
        """Regression: flat price, no dividends, every sleeve already
        holding its full target (A) from seeding -> value never moves."""
        divs = {"A": [], "B": []}
        history = backtest.run_tranched(candles_by_symbol, divs)

        assert len(history) >= 2
        for r in history:
            assert r.value == Decimal("10000000")


class TestRunTranchedTranchesParam:
    """tranches=(0,) is the supported way to get a single non-tranched
    monthly rebalance - this locks in that the override works and doesn't
    mutate config."""

    def test_explicit_tranches_overrides_config_without_mutating_it(
            self, candles_by_symbol, monkeypatch):
        monkeypatch.setattr(config, "TRANCHES", (0, 5, 10))
        divs = {"A": [], "B": []}

        backtest.run_tranched(candles_by_symbol, divs, tranches=(0,))

        assert config.TRANCHES == (0, 5, 10)  # untouched by the call

    def test_default_still_falls_back_to_config_tranches(
            self, candles_by_symbol, monkeypatch):
        monkeypatch.setattr(config, "TRANCHES", (0,))
        divs = {"A": [], "B": []}

        with_default = backtest.run_tranched(candles_by_symbol, divs)
        with_explicit = backtest.run_tranched(candles_by_symbol, divs, tranches=(0,))

        assert [r.date for r in with_default] == [r.date for r in with_explicit]
