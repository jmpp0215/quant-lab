"""Tranche bookkeeping for staggered rebalancing.

Capital is split into equally sized sleeves that rebalance on different
trading days of the month. Holdings are tracked per sleeve; cash is not,
since attributing every deposit to a sleeve costs more complexity than
the precision is worth. Cash is a pool owned by no sleeve.

A rebalancing sleeve sizes itself to 1/N of the whole account (every
sleeve's holdings plus the pool - `sleeve_budget`), buying from or selling
into the pool, so each rebalance pulls it back to an equal share. That is
what the staggering is for: spreading timing luck needs equal sleeves, not
exact ones.

This replaced "own holdings + 1/N of the pool" (until 2026-10-01). That
rule moved value between sleeves on every trade - a seller handed
(N-1)/N of its proceeds to the others, a buyer took (N-1)/N of its cost
from them - so sleeve sizes drifted apart (6.55M / 5.21M / 5.02M on
kis-isa after tranche 0's 2026-10-01 run), and the pool only drained by
1/N per rebalance, leaving ~1/3 of kis-isa idle for weeks. It also needed
every sleeve seeded at once (`tranche_init.py`) to start correctly; the
1/N-of-account rule does not.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from quant import config

log = logging.getLogger(__name__)


def split_evenly(quantity: int, parts: int) -> list[int]:
    """Divide whole shares across tranches, remainder to the earliest.

    Sleeves end up marginally different in size, which their own
    rebalances absorb over time.
    """
    base, remainder = divmod(quantity, parts)
    return [base + (1 if i < remainder else 0) for i in range(parts)]


def initial_split(actual: dict[str, int]) -> dict[int, dict[str, int]]:
    """Assign an existing position to tranches without trading."""
    tranches = config.TRANCHES
    out: dict[int, dict[str, int]] = {t: {} for t in tranches}

    for symbol, quantity in actual.items():
        for tranche, share in zip(tranches,
                                  split_evenly(quantity, len(tranches)),
                                  strict=True):
            if share:
                out[tranche][symbol] = share

    return out


def booked_total(holdings: dict[int, dict[str, int]]) -> dict[str, int]:
    """Sum across tranches, as the books say the account should look."""
    total: dict[str, int] = {}
    for sleeve in holdings.values():
        for symbol, quantity in sleeve.items():
            total[symbol] = total.get(symbol, 0) + quantity
    return total


def reconcile(holdings: dict[int, dict[str, int]],
              actual: dict[str, int]) -> dict[str, int]:
    """Actual account minus the tranche books, per symbol.

    A non-empty result means something moved outside the strategy: a
    manual trade, a dividend paid in shares, or a fill that was never
    recorded. Acting on stale books would compound the error, so callers
    should stop rather than trade through it.
    """
    booked = booked_total(holdings)
    drift = {}
    for symbol in set(booked) | set(actual):
        delta = actual.get(symbol, 0) - booked.get(symbol, 0)
        if delta:
            drift[symbol] = delta
    return drift


def trading_day_index(candles: list[dict], today: str) -> int | None:
    """Zero-based position of `today` among this month's trading days.

    Counted from candle history because the calendar API exposes only the
    current, previous, and next business day.
    """
    month = today[:7]
    days = sorted({
        c["timestamp"][:10] for c in candles
        if c["timestamp"][:10].startswith(month)
    })
    return days.index(today) if today in days else None


def due_today(day_index: int, done_this_month: set[int],
              month: str | None = None) -> int | None:
    """Which tranche should rebalance today, if any.

    A tranche whose scheduled day has passed without running catches up at
    the next opportunity: with monthly rebalancing, skipping a month costs
    more than drifting a day or two off schedule. The catch-up is
    suppressed before TRANCHE_START_MONTH, when sleeves had not yet been
    given a schedule to miss.
    """
    if month is not None and month < config.TRANCHE_START_MONTH:
        return day_index if day_index in config.TRANCHES else None

    for scheduled in sorted(config.TRANCHES):
        if scheduled in done_this_month:
            continue
        if day_index >= scheduled:
            return scheduled
    return None


def target_quantities(weights: dict[str, Decimal], value: Decimal,
                      prices: dict[str, Decimal]) -> dict[str, int]:
    """Whole-share targets for one tranche's share of the portfolio."""
    return {
        symbol: int(value * weight / prices[symbol])
        for symbol, weight in weights.items()
        if prices.get(symbol)
    }


def sleeve_equity(holdings: dict[str, int], prices: dict[str, Decimal]) -> Decimal:
    """Market value of one sleeve's own holdings. Cash belongs to no sleeve."""
    return sum(
        (Decimal(quantity) * prices[symbol]
         for symbol, quantity in holdings.items()
         if symbol in prices),
        Decimal("0"),
    )


@dataclass(frozen=True)
class Budget:
    target: Decimal          # 1/N of the whole account
    value: Decimal           # what the sleeve sizes to: target, or all it can reach
    deposit_needed: Decimal  # deposit that would let it reach target; 0 if it can


def sleeve_budget(books: dict[int, dict[str, int]], which: int,
                  prices: dict[str, Decimal], cash: Decimal,
                  n: int | None = None) -> Budget:
    """How much sleeve `which` should hold after rebalancing: 1/N of the
    account (every sleeve's holdings plus the cash pool).

    The most it can reach is its own holdings plus all the cash. When that
    falls short, it sizes to what it can reach and reports the deposit
    that would close the gap - which is gap * N / (N - 1), not the gap,
    because a deposit also raises the account and so the 1/N target.
    """
    n = n if n is not None else len(config.TRANCHES)
    account = sum((sleeve_equity(b, prices) for b in books.values()),
                  Decimal("0")) + cash
    target = account / n
    reachable = sleeve_equity(books.get(which, {}), prices) + cash
    if target <= reachable:
        return Budget(target=target, value=target, deposit_needed=Decimal("0"))
    return Budget(target=target, value=reachable,
                  deposit_needed=(target - reachable) * n / (n - 1))


def next_due(candles: list[dict], today: str,
             done_this_month: set[int]) -> tuple[str, int] | None:
    """The next trading date on which a tranche is scheduled, and which.

    Looks only within the current month: the schedule resets each month,
    and a tranche that has not run by month end is simply skipped rather
    than carried over.
    """
    month = today[:7]
    days = sorted({
        c["timestamp"][:10] for c in candles
        if c["timestamp"][:10].startswith(month)
    })

    for index, date in enumerate(days):
        if date <= today:
            continue
        which = due_today(index, done_this_month)
        if which is not None:
            return date, which

    return None