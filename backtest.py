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
from decimal import Decimal

import config
import strategy

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


def close_at(candles: list[dict], as_of: str) -> Decimal | None:
    """Closing price on the last trading day at or before `as_of`."""
    sliced = slice_at(candles, as_of)
    return Decimal(sliced[0]["closePrice"]) if sliced else None


def rebalance_dates(candles_by_symbol: dict[str, list[dict]],
                    skip_months: int = 13) -> list[str]:
    """First trading date of each month, once enough history has accrued.

    The reference symbol drives the calendar; every symbol in the universe
    is Korea-listed, so they share one.
    """
    reference = candles_by_symbol[next(iter(config.UNIVERSE))]
    dates = sorted(_date_of(c) for c in reference)

    firsts: list[str] = []
    seen: set[str] = set()
    for d in dates:
        month = d[:7]
        if month not in seen:
            seen.add(month)
            firsts.append(d)

    return firsts[skip_months:]


def run(candles_by_symbol: dict[str, list[dict]],
        initial: Decimal = Decimal("10000000"),
        scheme: str = "equal") -> list[Rebalance]:
    """Replay monthly rebalances, holding the selected names in between."""
    history: list[Rebalance] = []
    value = initial
    holdings: dict[str, Decimal] = {}

    for date in rebalance_dates(candles_by_symbol):
        prices = {
            sym: close_at(cs, date)
            for sym, cs in candles_by_symbol.items()
        }
        prices = {s: p for s, p in prices.items() if p is not None}

        if holdings:
            value = sum(
                units * prices[sym]
                for sym, units in holdings.items()
                if sym in prices
            )

        sliced = {
            sym: slice_at(cs, date)
            for sym, cs in candles_by_symbol.items()
        }
        signal = strategy.evaluate(sliced)

        weights = signal.weights
        if scheme != "equal" and weights:
            scores = {s.symbol: s.momentum for s in signal.scores
                      if s.momentum is not None}
            weights = strategy._weights_by_scheme(
                sliced, list(weights), scheme, scores)

        holdings = {
            sym: (value * weight) / prices[sym]
            for sym, weight in weights.items()
            if sym in prices
        }

        history.append(Rebalance(date=date, weights=weights,
                                 prices=prices, value=value))

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
        history.append(Rebalance(date=last, weights={}, prices=final_prices,
                                 value=value))

    return history


def summarise(history: list[Rebalance], label: str = "") -> str:
    if len(history) < 2:
        return "not enough history"

    start, end = history[0].value, history[-1].value
    total = (end - start) / start

    # Period returns between rebalances, for a rough volatility estimate.
    steps = [
        (history[i + 1].value - history[i].value) / history[i].value
        for i in range(len(history) - 1)
    ]
    mean = sum(steps) / len(steps)
    if len(steps) > 1:
        variance = sum((s - mean) ** 2 for s in steps) / (len(steps) - 1)
        # Monthly observations, so annualise by sqrt(12).
        vol = Decimal(str(float(variance) ** 0.5)) * Decimal("3.4641")
    else:
        vol = Decimal("0")

    periods = Decimal(len(steps))
    annualised = (Decimal("1") + total) ** (Decimal("12") / periods) - 1
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
