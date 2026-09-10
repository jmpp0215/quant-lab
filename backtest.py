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

def _spread_cost(symbol: str, notional: Decimal) -> Decimal:
    """One-way cost of crossing the spread."""
    half = config.HALF_SPREAD.get(symbol, config.DEFAULT_HALF_SPREAD)
    return notional * half


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

    dividend_events_by_symbol: still required by the signature but unused.
    Candles are adjusted prices, so distributions are already in the price
    path - adding the payout as cash on top would double-count it, the
    same reason strategy.evaluate() dropped its yield term (see CLAUDE.md).

    tranches: defaults to config.TRANCHES. Pass tranches=(0,) for a single
    monthly rebalance (first trading day of each month) with correct cash
    accounting, without having to mutate config.TRANCHES globally to get a
    one-off single-sleeve check.
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

    for trade_date, which in schedule:
        prices = {
            sym: close_at(cs, trade_date)
            for sym, cs in candles_by_symbol.items()
        }
        prices = {s: p for s, p in prices.items() if p is not None}
        prices["cash"] = Decimal("1.0")

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