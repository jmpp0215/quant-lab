"""Historical replay of the strategy.

Only the candles that existed on each decision date are passed in, so the
strategy cannot see prices it would not have had. This is the same slicing
a backfill needs, which is why it lives in its own module rather than
inside a one-off script.

With ~19 months of candles and a 12-month lookback there are only about
seven monthly decisions here. That is far too few to say anything about
whether the strategy works; the point is to confirm the machinery runs and
to see whether the ranking ever actually rotates.
"""

import logging
import warnings
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from quant import config, strategy

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rebalance:
    date: str
    weights: dict[str, Decimal]
    prices: dict[str, Decimal]
    value: Decimal


def _date_of(candle: dict) -> str:
    return candle["timestamp"][:10]


def slice_at(candles: list[dict], as_of: str) -> list[dict]:
    """Candles as they would have looked on `as_of`, newest first."""
    return [c for c in candles if _date_of(c) <= as_of]


def slice_dividends_at(events: list[dict], as_of: str) -> list[dict]:
    """Dividend events known as of `as_of` - the same no-look-ahead slice
    slice_at() applies to candles, applied to payout history instead."""
    return [e for e in events if e["record_date"] <= as_of]


def close_at(candles: list[dict], as_of: str) -> Decimal | None:
    """Closing price on the last trading day at or before `as_of`."""
    sliced = slice_at(candles, as_of)
    return Decimal(sliced[0]["closePrice"]) if sliced else None


def dividend_income(holdings: dict[str, Decimal],
                    dividend_events_by_symbol: dict[str, list[dict]],
                    since: str, until: str) -> Decimal:
    """Cash paid on continuously-held `holdings` with record_date in
    (since, until] - since is exclusive so a dividend already counted in
    the prior period is never counted twice, until is inclusive to match
    slice_dividends_at()'s own `<= as_of` convention. Backtest counterpart
    to quant/momentum.py's trailing_yield(), which uses the same bounds
    for the same reason: the ex-dividend price drop is already in the
    candles, so this is the only place the payout itself gets counted.
    """
    total = Decimal("0")
    for sym, units in holdings.items():
        if units <= 0:
            continue
        events = dividend_events_by_symbol.get(sym, [])
        paid = sum(
            (e["amount"] for e in events if since < e["record_date"] <= until),
            Decimal("0"),
        )
        total += units * paid
    return total


def rebalance_dates(candles_by_symbol: dict[str, list[dict]],
                    skip_months: int = 13,
                    offset: int = 0) -> list[str]:
    """The (offset+1)-th trading date of each month, once history allows.

    Months without enough trading days are skipped rather than falling
    back to their last day: substituting a different day would quietly
    turn a timing-luck comparison into a comparison of different
    schedules.
    """
    reference = candles_by_symbol[next(iter(config.UNIVERSE))]
    dates = sorted(_date_of(c) for c in reference)

    by_month: dict[str, list[str]] = {}
    for d in dates:
        by_month.setdefault(d[:7], []).append(d)

    months = sorted(by_month)[skip_months:]
    return [
        by_month[m][offset] for m in months
        if len(by_month[m]) > offset
    ]

def run(candles_by_symbol: dict[str, list[dict]],
        dividend_events_by_symbol: dict[str, list[dict]],
        initial: Decimal = Decimal("10000000"),
        scheme: str = "equal",
        offset: int = 0,
        costs: bool = False) -> list[Rebalance]:
    """DEPRECATED: use run_tranched(tranches=(0,)) instead - confirmed to
    produce identical numbers for every case that doesn't trigger the bug
    below, and correct ones for the cases that do. Kept only because
    tests/test_backtest.py still exercises it; not removed since deleting
    a working (if bugged) function that nothing currently imports isn't
    worth the churn.

    Bug: this function has no cash ledger at all - value is recomputed
    purely from mark-to-market of `holdings` each rebalance. If
    strategy.evaluate()'s weights ever sum to less than 1 (the absolute
    momentum filter leaving fewer than TOP_N names eligible), the
    unallocated fraction is not carried forward as cash - it is silently
    dropped from value on the next rebalance, compounding every period
    after. Confirmed by direct reproduction: flat prices, weights summing
    to 2/3 every rebalance, 10,000,000 -> 585,277 after 8 rebalances
    (should stay flat at 10,000,000). run_tranched() has no such bug -
    it debits a real `cash` variable only for what actually gets bought,
    so an unallocated fraction naturally stays in cash. Never observed to
    have actually triggered in this project's history (cached candles or
    daily.py's recorded scores), but is a live risk for any period where
    it does.

    Replay monthly rebalances, holding the selected names in between.

    With costs=True, each rebalance pays the spread on both sides and tax
    on realised gains in foreign-tracking ETFs. Turnover is what makes
    tranching expensive, so comparing schedules without it is misleading.

    dividend_events_by_symbol: raw payout history per symbol - required,
    not defaulted, so a forgotten argument errors instead of silently
    producing a zero-dividend backtest.
    """
    warnings.warn(
        "backtest.run() has a bug that silently loses uninvested capital "
        "when signal weights sum to less than 1 (see docstring) - use "
        "run_tranched(tranches=(0,)) instead.",
        DeprecationWarning, stacklevel=2,
    )
    history: list[Rebalance] = []
    value = initial
    holdings: dict[str, Decimal] = {}
    basis: dict[str, Decimal] = {}      # symbol -> cost per unit
    previous_date: str | None = None    # start of the current holding period

    for trade_date in rebalance_dates(candles_by_symbol, offset=offset):
        prices = {
            sym: close_at(cs, trade_date)
            for sym, cs in candles_by_symbol.items()
        }
        prices = {s: p for s, p in prices.items() if p is not None}

        if holdings:
            value = sum(
                units * prices[sym]
                for sym, units in holdings.items()
                if sym in prices
            )
            # There is no separate cash ledger here - the whole portfolio
            # is always fully reinvested at each rebalance - so dividends
            # collected while holding `holdings` are folded straight into
            # value before it gets split into the new target weights below.
            if previous_date is not None:
                value += dividend_income(
                    holdings, dividend_events_by_symbol, previous_date, trade_date)

        sliced = {
            sym: slice_at(cs, trade_date)
            for sym, cs in candles_by_symbol.items()
        }
        sliced_dividends = {
            sym: slice_dividends_at(evs, trade_date)
            for sym, evs in dividend_events_by_symbol.items()
        }
        signal = strategy.evaluate(sliced, sliced_dividends)

        weights = signal.weights
        if scheme != "equal" and weights:
            scores = {s.symbol: s.momentum for s in signal.scores
                      if s.momentum is not None}
            weights = strategy._weights_by_scheme(
                sliced, list(weights), scheme, scores)

        target = {
            sym: (value * weight) / prices[sym]
            for sym, weight in weights.items()
            if sym in prices
        }

        if costs:
            value -= _rebalance_cost(holdings, target, prices, basis)
            target = {
                sym: (value * weight) / prices[sym]
                for sym, weight in weights.items()
                if sym in prices
            }

        # Carry basis forward for held units, set it for newly bought ones.
        for sym, units in target.items():
            previous = holdings.get(sym, Decimal("0"))
            if units > previous:
                bought = units - previous
                old_cost = previous * basis.get(sym, prices[sym])
                basis[sym] = (old_cost + bought * prices[sym]) / units
            elif sym not in basis:
                basis[sym] = prices[sym]

        holdings = target
        history.append(Rebalance(date=trade_date, weights=weights,
                                 prices=prices, value=value))
        previous_date = trade_date

    if holdings:
        last = max(_date_of(c) for c in
                   candles_by_symbol[next(iter(config.UNIVERSE))])
        final_prices = {
            sym: close_at(cs, last)
            for sym, cs in candles_by_symbol.items()
        }
        value = sum(units * final_prices[sym]
                    for sym, units in holdings.items()
                    if final_prices.get(sym))
        if previous_date is not None:
            value += dividend_income(
                holdings, dividend_events_by_symbol, previous_date, last)
        history.append(Rebalance(date=last, weights={}, prices=final_prices,
                                 value=value))

    return history


def _rebalance_cost(current: dict[str, Decimal], target: dict[str, Decimal],
                    prices: dict[str, Decimal],
                    basis: dict[str, Decimal]) -> Decimal:
    """Total cost of moving from `current` to `target` holdings."""
    total = Decimal("0")

    for sym in set(current) | set(target):
        if sym not in prices:
            continue
        delta = target.get(sym, Decimal("0")) - current.get(sym, Decimal("0"))
        if delta == 0:
            continue

        notional = abs(delta) * prices[sym]
        total += _spread_cost(sym, notional)

        if delta < 0:
            sold = abs(delta)
            cost_basis = sold * basis.get(sym, prices[sym])
            total += _tax_on_sale(sym, sold * prices[sym], cost_basis)

    return total


def summarise(history: list[Rebalance], label: str = "") -> str:
    if len(history) < 2:
        return "not enough history"

    start, end = history[0].value, history[-1].value
    total = (end - start) / start

    steps = [
        (history[i + 1].value - history[i].value) / history[i].value
        for i in range(len(history) - 1)
    ]

    # Annualise from elapsed calendar time, not the number of
    # observations: tranched runs record three times as many points over
    # the same period, and counting them as months understates the rate.
    first = date.fromisoformat(history[0].date)
    last = date.fromisoformat(history[-1].date)
    years = Decimal((last - first).days) / Decimal("365.25")
    if years <= 0:
        return "not enough history"

    annualised = (Decimal("1") + total) ** (1 / years) - 1

    periods_per_year = Decimal(len(steps)) / years
    mean = sum(steps) / len(steps)
    if len(steps) > 1:
        variance = sum((s - mean) ** 2 for s in steps) / (len(steps) - 1)
        vol = Decimal(str(float(variance) ** 0.5)) * Decimal(
            str(float(periods_per_year) ** 0.5))
    else:
        vol = Decimal("0")

    sharpe = annualised / vol if vol else Decimal("0")

    peak = start
    max_dd = Decimal("0")
    for r in history:
        peak = max(peak, r.value)
        max_dd = min(max_dd, (r.value - peak) / peak)

    return (
        f"{label:<16} return={total:>7.2%}  ann={annualised:>7.2%}  "
        f"vol={vol:>6.2%}  sharpe={sharpe:>5.2f}  mdd={max_dd:>7.2%}"
    )

def buy_and_hold(candles_by_symbol: dict[str, list[dict]], symbol: str,
                 dates: list[str],
                 initial: Decimal = Decimal("10000000")) -> Decimal:
    """Value of holding one symbol for the whole period."""
    cs = candles_by_symbol[symbol]
    start = close_at(cs, dates[0])
    end = close_at(cs, dates[-1])
    if not start or not end:
        return initial
    return initial * end / start


def equal_weight(candles_by_symbol: dict[str, list[dict]],
                 dates: list[str],
                 initial: Decimal = Decimal("10000000")) -> Decimal:
    """Value of holding the whole universe equally, never rebalancing.

    This is the benchmark that matters most: it isolates what the ranking
    contributed, as opposed to simply being long these assets.
    """
    symbols = [s for s in config.UNIVERSE
               if close_at(candles_by_symbol[s], dates[0])]
    per = initial / len(symbols)
    total = Decimal("0")
    for sym in symbols:
        cs = candles_by_symbol[sym]
        start, end = close_at(cs, dates[0]), close_at(cs, dates[-1])
        if start and end:
            total += per * end / start
    return total

def _spread_cost(symbol: str, notional: Decimal) -> Decimal:
    """One-way cost of crossing the spread."""
    half = config.HALF_SPREAD.get(symbol, config.DEFAULT_HALF_SPREAD)
    return notional * half


def _tax_on_sale(symbol: str, proceeds: Decimal,
                 cost_basis: Decimal) -> Decimal:
    """Tax due on realising a gain.

    Only foreign-tracking ETFs are taxed; a domestic equity ETF can be
    rotated freely. Losses are treated as zero rather than as a credit,
    which understates the benefit of loss harvesting but avoids modelling
    an annual offset the backtest has no way to track.
    """
    if symbol not in config.FOREIGN_ETF:
        return Decimal("0")
    gain = proceeds - cost_basis
    return gain * config.GAINS_TAX_RATE if gain > 0 else Decimal("0")

def _pooled_holdings(books: dict[int, dict[str, Decimal]]) -> dict[str, Decimal]:
    """Units held per symbol, summed across every sleeve's book."""
    pooled: dict[str, Decimal] = {}
    for book in books.values():
        for sym, units in book.items():
            pooled[sym] = pooled.get(sym, Decimal("0")) + units
    return pooled


def run_tranched(candles_by_symbol: dict[str, list[dict]],
                 dividend_events_by_symbol: dict[str, list[dict]],
                 initial: Decimal = Decimal("10000000"),
                 scheme: str = "equal",
                 costs: bool = False,
                 tranches: tuple[int, ...] | None = None) -> list[Rebalance]:
    """Replay the strategy with capital split across staggered sleeves.

    Each sleeve rebalances on its own trading day of the month and holds
    its positions untouched in between. Cash is pooled: a sleeve treats
    1/N of the balance as its own, matching how the live system works.

    dividend_events_by_symbol: see run() - required, not defaulted.

    tranches: defaults to config.TRANCHES. Pass tranches=(0,) for a single
    monthly rebalance with correct cash accounting - this reproduces run()'s
    numbers exactly (see run()'s docstring) without having to mutate
    config.TRANCHES globally to get a one-off single-sleeve check.
    """
    tranches = list(tranches) if tranches is not None else list(config.TRANCHES)
    n = len(tranches)

    books: dict[int, dict[str, Decimal]] = {t: {} for t in tranches}
    cash = initial
    history: list[Rebalance] = []

    schedule = _tranche_schedule(candles_by_symbol, tranches)
    if not schedule:
        return []

    # Seed every sleeve on the first scheduled date. The live system was
    # already fully invested when tranching began, so starting from cash
    # would charge the comparison for three weeks of sitting out.
    first_date = schedule[0][0]
    seed_prices = {sym: close_at(cs, first_date)
                   for sym, cs in candles_by_symbol.items()}
    seed_prices = {s: p for s, p in seed_prices.items() if p is not None}

    seed_sliced = {sym: slice_at(cs, first_date)
                   for sym, cs in candles_by_symbol.items()}
    seed_sliced_dividends = {
        sym: slice_dividends_at(evs, first_date)
        for sym, evs in dividend_events_by_symbol.items()
    }
    seed_signal = strategy.evaluate(seed_sliced, seed_sliced_dividends)

    per_sleeve = initial / n
    for t in tranches:
        books[t] = {
            sym: (per_sleeve * weight) / seed_prices[sym]
            for sym, weight in seed_signal.weights.items()
            if sym in seed_prices
        }
        cash -= sum(units * seed_prices[sym]
                for sym, units in books[t].items())

    # The first scheduled rebalance is now a no-op for that sleeve.
    schedule = schedule[1:]

    # Tracks how far dividend income has been collected up to - cash is
    # pooled across sleeves already, so dividends are too: every sleeve's
    # current holdings are checked together at each step rather than
    # attributed to whichever sleeve happens to be rebalancing that day.
    last_dividend_check = first_date

    for trade_date, which in schedule:
        prices = {
            sym: close_at(cs, trade_date)
            for sym, cs in candles_by_symbol.items()
        }
        prices = {s: p for s, p in prices.items() if p is not None}
        prices["cash"] = Decimal("1.0")

        cash += dividend_income(
            _pooled_holdings(books), dividend_events_by_symbol,
            last_dividend_check, trade_date)
        last_dividend_check = trade_date

        sliced = {
            sym: slice_at(cs, trade_date)
            for sym, cs in candles_by_symbol.items()
        }
        sliced_dividends = {
            sym: slice_dividends_at(evs, trade_date)
            for sym, evs in dividend_events_by_symbol.items()
        }
        signal = strategy.evaluate(sliced, sliced_dividends)

        weights = signal.weights
        if scheme != "equal" and weights:
            scores = {s.symbol: s.momentum for s in signal.scores
                      if s.momentum is not None}
            weights = strategy._weights_by_scheme(
                sliced, list(weights), scheme, scores)

        book = books[which]
        equity = sum(units * prices[sym] for sym, units in book.items()
                     if sym in prices)
        sleeve_value = equity + cash / n

        target = {
            sym: (sleeve_value * weight) / prices[sym]
            for sym, weight in weights.items()
            if sym in prices
        }

        # Trades settle against the shared cash pool.
        for sym in set(book) | set(target):
            if sym not in prices:
                continue
            delta = target.get(sym, Decimal("0")) - book.get(sym, Decimal("0"))
            if delta == 0:
                continue
            notional = delta * prices[sym]
            cash -= notional
            if costs:
                cash -= _spread_cost(sym, abs(notional))

        books[which] = {s: u for s, u in target.items() if u > 0}

        total = cash + sum(
            units * prices[sym]
            for b in books.values()
            for sym, units in b.items()
            if sym in prices
        )
        history.append(Rebalance(date=trade_date, weights=weights,
                                 prices=prices, value=total))

    last = max(_date_of(c) for c in
               candles_by_symbol[next(iter(config.UNIVERSE))])
    final_prices = {sym: close_at(cs, last)
                    for sym, cs in candles_by_symbol.items()}
    cash += dividend_income(
        _pooled_holdings(books), dividend_events_by_symbol,
        last_dividend_check, last)
    total = cash + sum(
        units * final_prices[sym]
        for b in books.values()
        for sym, units in b.items()
        if final_prices.get(sym)
    )
    history.append(Rebalance(date=last, weights={}, prices=final_prices,
                             value=total))

    return history


def _tranche_schedule(candles_by_symbol: dict[str, list[dict]],
                      tranches: list[int],
                      skip_months: int = 13) -> list[tuple[str, int]]:
    """(date, tranche) pairs in chronological order."""
    reference = candles_by_symbol[next(iter(config.UNIVERSE))]
    dates = sorted(_date_of(c) for c in reference)

    by_month: dict[str, list[str]] = {}
    for d in dates:
        by_month.setdefault(d[:7], []).append(d)

    out = []
    for month in sorted(by_month)[skip_months:]:
        days = by_month[month]
        for t in tranches:
            if len(days) > t:
                out.append((days[t], t))

    return sorted(out)