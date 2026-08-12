"""Dual momentum strategy.

Pure functions only - no network, no clock, no file access. Everything
comes in as arguments so the same code can be driven by live data or by
historical data during a backtest.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

import config
import momentum

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Score:
    symbol: str
    name: str
    momentum: Decimal | None

    @property
    def ranked(self) -> bool:
        """Symbols without enough history cannot be compared fairly."""
        return self.momentum is not None


@dataclass(frozen=True)
class Signal:
    """The strategy's output: what the portfolio should look like."""
    weights: dict[str, Decimal]      # symbol -> target weight
    cash_weight = Decimal("1") - sum(weights.values(), Decimal("0"))
    # Equal weights of 1/3 leave a rounding tail; anything below a basis
    # point is not a real cash allocation.
    if abs(cash_weight) < Decimal("0.0001"):
        cash_weight = Decimal("0")

def evaluate(candles_by_symbol: dict[str, list[dict]]) -> Signal:
    """Rank the universe by momentum and pick the top N.

    Absolute momentum is applied by requiring a symbol to beat the cash
    proxy, not merely to be positive - a 2% gain is not worth holding when
    risk-free cash returns 3%.
    """
    scores = [
        Score(
            symbol=sym,
            name=name,
            momentum=momentum.total_return(
                candles_by_symbol.get(sym, []),
                config.LOOKBACK_MONTHS,
                config.SKIP_MONTHS,
                config.DIVIDEND_YIELD.get(sym, Decimal("0")),
            ),
        )
        for sym, name in config.UNIVERSE.items()
    ]
    scores.sort(key=lambda s: s.momentum if s.ranked else Decimal("-999"),
                reverse=True)

    cash_score = next(
        (s.momentum for s in scores if s.symbol == config.CASH_SYMBOL), None
    )
    hurdle = cash_score if cash_score is not None else Decimal("0")

    # Absolute momentum filter, then relative momentum selection.
    eligible = [
        s for s in scores
        if s.ranked and s.momentum > hurdle and s.symbol != config.CASH_SYMBOL
    ]
    selected = eligible[:config.TOP_N]

    weight = Decimal("1") / config.TOP_N
    weights = {s.symbol: weight for s in selected}

    # Slots that found no qualifying symbol stay in cash rather than being
    # redistributed - concentrating into fewer names would raise risk at
    # exactly the moment the market is weak.
    cash_weight = Decimal("1") - sum(weights.values(), Decimal("0"))

    return Signal(weights=weights, cash_weight=cash_weight, scores=scores)


def format_signal(signal: Signal) -> str:
    """Human-readable ranking for the daily log."""
    lines = ["momentum ranking:"]
    for i, s in enumerate(signal.scores, 1):
        mom = f"{s.momentum:>8.2%}" if s.ranked else "     n/a"
        mark = "*" if s.symbol in signal.weights else " "
        lines.append(f"  {mark}{i}. {s.name:<24} {mom}")
    lines.append(f"  cash: {signal.cash_weight:.0%}")
    return "\n".join(lines)