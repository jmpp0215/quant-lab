"""Monthly-accumulation timing backtest (pure, no I/O).

Question: a fixed amount is going into AMZN every month regardless - does
choosing *which day* in the month to buy lower the average cost versus
simply buying on the month's first trading day?

Execution model: a rule looks at closes up to and including day i and, if
it fires, the buy happens at day i+1's open (signal at the close, order at
the next session's open - what's actually doable from Korea). A rule that
never fires in a month buys at the month's last trading day's open, so cash
never sits idle past the month and every strategy buys exactly once a month.
Fractional shares, no costs - this compares prices paid, nothing else.
"""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from quant.amzn.signal import compute_rsi


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    close: float


@dataclass(frozen=True)
class Buy:
    month: tuple[int, int]
    day: date
    price: float


# Picks the bar index to buy at, given all bars and one month's indexes.
DayPicker = Callable[[list[Bar], list[int]], int]
# Whether a rule fires at the close of bars[i], using bars[:i + 1] only.
Trigger = Callable[[list[Bar], int], bool]


def month_groups(bars: list[Bar]) -> dict[tuple[int, int], list[int]]:
    groups: dict[tuple[int, int], list[int]] = {}
    for i, bar in enumerate(bars):
        groups.setdefault((bar.day.year, bar.day.month), []).append(i)
    return groups


def first_day(bars: list[Bar], idx: list[int]) -> int:
    return idx[0]


def last_day(bars: list[Bar], idx: list[int]) -> int:
    return idx[-1]


def hindsight_low(bars: list[Bar], idx: list[int]) -> int:
    """Lowest open of the month - not achievable, an upper bound on what
    any timing rule could gain."""
    return min(idx, key=lambda i: bars[i].open)


def triggered(trigger: Trigger) -> DayPicker:
    def pick(bars: list[Bar], idx: list[int]) -> int:
        for k in range(len(idx) - 1):
            if trigger(bars, idx[k]):
                return idx[k + 1]
        return idx[-1]
    return pick


def rsi_below(threshold: float) -> Trigger:
    def fires(bars: list[Bar], i: int) -> bool:
        rsi = compute_rsi([b.close for b in bars[:i + 1]])
        return rsi is not None and rsi < threshold
    return fires


def drawdown_from_high(window: int, pct: float) -> Trigger:
    """Close at least `pct` (e.g. 0.05) below the highest close of the last
    `window` days including today."""
    def fires(bars: list[Bar], i: int) -> bool:
        if i + 1 < window:
            return False
        high = max(b.close for b in bars[i + 1 - window:i + 1])
        return bars[i].close <= high * (1 - pct)
    return fires


def simulate(bars: list[Bar], months: list[tuple[int, int]],
             picker: DayPicker) -> list[Buy]:
    groups = month_groups(bars)
    buys = []
    for m in months:
        i = picker(bars, groups[m])
        buys.append(Buy(m, bars[i].day, bars[i].open))
    return buys


@dataclass(frozen=True)
class Comparison:
    share_gain: float           # shares bought vs baseline, -1..; 0.01 = +1%
    monthly_win_rate: float     # months where price paid < baseline's
    yearly: dict[int, float]    # share_gain per calendar year


def _shares(buys: list[Buy]) -> float:
    return sum(1 / b.price for b in buys)  # 1 unit of cash per month


def compare(buys: list[Buy], baseline: list[Buy]) -> Comparison:
    """Same months on both sides; the same amount each month means more
    shares == lower average cost."""
    assert [b.month for b in buys] == [b.month for b in baseline]
    wins = sum(b.price < base.price for b, base in zip(buys, baseline, strict=True))
    yearly = {}
    for year in sorted({b.month[0] for b in buys}):
        mine = [b for b in buys if b.month[0] == year]
        theirs = [b for b in baseline if b.month[0] == year]
        yearly[year] = _shares(mine) / _shares(theirs) - 1
    return Comparison(
        share_gain=_shares(buys) / _shares(baseline) - 1,
        monthly_win_rate=wins / len(buys),
        yearly=yearly,
    )
