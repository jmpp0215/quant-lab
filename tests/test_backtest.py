"""Tests for backtest.py's cash/value bookkeeping.

run_tranched() drives strategy.evaluate() for real, but that pulls in
config.UNIVERSE/CASH_SYMBOL/LOOKBACK_MONTHS and months of realistic
multi-symbol history to produce a ranking - none of which this module
needs to exercise. strategy.evaluate is monkeypatched to a fixed "always
100% symbol A" signal so the holding schedule is deterministic, and
prices are held flat so value only moves if the bookkeeping moves it.

Candles are adjusted prices, so distributions are already in the price
path: run_tranched no longer adds payout cash (that was a double-count),
and these tests pin that a dividend event changes nothing.
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
    holding schedule deterministic so a flat price isolates the
    bookkeeping exactly."""
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


class TestRunTranchedIgnoresDividends:
    def test_mid_run_dividend_event_changes_nothing(self, candles_by_symbol):
        schedule = backtest._tranche_schedule(candles_by_symbol, list(config.TRANCHES))
        assert len(schedule) >= 3, "fixture needs a seed date plus 2 loop iterations"

        second_date, third_date = (
            date.fromisoformat(schedule[1][0]),
            date.fromisoformat(schedule[2][0]),
        )
        mid = second_date + timedelta(days=1)
        while mid >= third_date or mid.weekday() >= 5:
            mid -= timedelta(days=1)
        assert second_date < mid < third_date

        with_div = backtest.run_tranched(
            candles_by_symbol, {"A": events([(mid.isoformat(), "2")]), "B": []})
        without = backtest.run_tranched(candles_by_symbol, {"A": [], "B": []})

        assert [r.value for r in with_div] == [r.value for r in without]

    def test_final_valuation_ignores_a_late_dividend_event(self, candles_by_symbol):
        schedule = backtest._tranche_schedule(candles_by_symbol, list(config.TRANCHES))
        last_date = date.fromisoformat(schedule[-1][0])
        after_last = last_date + timedelta(days=1)
        while after_last.weekday() >= 5:
            after_last += timedelta(days=1)

        with_div = backtest.run_tranched(
            candles_by_symbol,
            {"A": events([(after_last.isoformat(), "3")]), "B": []})
        without = backtest.run_tranched(candles_by_symbol, {"A": [], "B": []})

        assert with_div[-1].value == without[-1].value

    def test_flat_price_no_events_value_never_moves(self, candles_by_symbol):
        """Regression: flat price, no dividends, every sleeve already
        holding its full target (A) from seeding -> value never moves."""
        history = backtest.run_tranched(candles_by_symbol, {"A": [], "B": []})

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
