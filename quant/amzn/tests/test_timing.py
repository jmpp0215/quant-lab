from datetime import date

import pytest

from quant.amzn import timing
from quant.amzn.timing import Bar


def _bars(spec: list[tuple[str, float, float]]) -> list[Bar]:
    return [Bar(date.fromisoformat(d), o, c) for d, o, c in spec]


# Two months, three trading days each.
BARS = _bars([
    ("2026-01-02", 100, 101),
    ("2026-01-05", 95, 90),
    ("2026-01-30", 92, 93),
    ("2026-02-02", 110, 111),
    ("2026-02-03", 105, 104),
    ("2026-02-27", 120, 121),
])
MONTHS = [(2026, 1), (2026, 2)]


class TestMonthGroups:
    def test_groups_by_calendar_month(self):
        assert timing.month_groups(BARS) == {(2026, 1): [0, 1, 2], (2026, 2): [3, 4, 5]}


class TestPickers:
    def test_first_and_last_day_buy_at_open(self):
        assert [b.price for b in timing.simulate(BARS, MONTHS, timing.first_day)] == [100, 110]
        assert [b.price for b in timing.simulate(BARS, MONTHS, timing.last_day)] == [92, 120]

    def test_hindsight_low_is_lowest_open(self):
        assert [b.price for b in timing.simulate(BARS, MONTHS, timing.hindsight_low)] == [92, 105]

    def test_trigger_buys_next_day_open(self):
        # fires at the close of 01-05 -> buys 01-30's open, not 01-05's
        fires_on_0105 = lambda bars, i: bars[i].day == date(2026, 1, 5)  # noqa: E731
        buys = timing.simulate(BARS, MONTHS, timing.triggered(fires_on_0105))
        assert (buys[0].day, buys[0].price) == (date(2026, 1, 30), 92)

    def test_no_trigger_falls_back_to_last_day(self):
        never = lambda bars, i: False  # noqa: E731
        buys = timing.simulate(BARS, MONTHS, timing.triggered(never))
        assert [b.day for b in buys] == [date(2026, 1, 30), date(2026, 2, 27)]

    def test_trigger_on_last_day_cannot_reach_next_month(self):
        # a signal at the month's final close has no next day inside the month
        last_only = lambda bars, i: bars[i].day == date(2026, 1, 30)  # noqa: E731
        buys = timing.simulate(BARS, MONTHS, timing.triggered(last_only))
        assert buys[1].day == date(2026, 2, 27)  # Feb didn't inherit Jan's signal


class TestDrawdown:
    def test_fires_at_threshold(self):
        bars = _bars([("2026-01-01", 0, 100), ("2026-01-02", 0, 95)])
        assert timing.drawdown_from_high(2, 0.05)(bars, 1)
        assert not timing.drawdown_from_high(2, 0.06)(bars, 1)

    def test_needs_full_window(self):
        bars = _bars([("2026-01-01", 0, 100), ("2026-01-02", 0, 50)])
        assert not timing.drawdown_from_high(3, 0.05)(bars, 1)

    def test_high_outside_window_ignored(self):
        bars = _bars([("2026-01-01", 0, 200), ("2026-01-02", 0, 100), ("2026-01-05", 0, 97)])
        assert not timing.drawdown_from_high(2, 0.05)(bars, 2)


class TestRsiBelow:
    def test_falling_series_fires_and_short_history_does_not(self):
        bars = _bars([(f"2026-01-{d:02d}", 0, 130 - 2 * d) for d in range(1, 16)])
        assert timing.rsi_below(30)(bars, 14)
        assert not timing.rsi_below(30)(bars, 13)  # only 14 closes -> RSI None


class TestCompare:
    def test_share_gain_and_win_rate(self):
        base = timing.simulate(BARS, MONTHS, timing.first_day)    # 100, 110
        other = timing.simulate(BARS, MONTHS, timing.last_day)    # 92, 120
        c = timing.compare(other, base)
        expected = (1 / 92 + 1 / 120) / (1 / 100 + 1 / 110) - 1
        assert c.share_gain == pytest.approx(expected)
        assert c.monthly_win_rate == 0.5
        assert c.yearly == {2026: pytest.approx(expected)}

    def test_identical_is_zero(self):
        base = timing.simulate(BARS, MONTHS, timing.first_day)
        assert timing.compare(base, base).share_gain == 0

    def test_mismatched_months_rejected(self):
        base = timing.simulate(BARS, MONTHS, timing.first_day)
        with pytest.raises(AssertionError):
            timing.compare(base[:1], base)
