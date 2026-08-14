"""Dual momentum strategy.

Pure functions only - no network, no clock, no file access. Everything
comes in as arguments so the same code can be driven by live data or by
historical data during a backtest.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
import indicators
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
    cash_weight: Decimal             # unallocated, held as cash
    scores: list[Score]              # full ranking, for the log

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

    cash_weight = Decimal("1") - sum(weights.values(), Decimal("0"))
    # Equal weights of 1/3 leave a rounding tail; anything below a basis
    # point is not a real cash allocation.
    if abs(cash_weight) < Decimal("0.0001"):
        cash_weight = Decimal("0")

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


@dataclass(frozen=True)
class Variant:
    """An alternative allocation, computed but never traded.

    Recorded daily so that months from now the choice between them can be
    settled against observations rather than argued from a backtest of
    eight rebalances.
    """
    name: str
    weights: dict[str, Decimal]


def _rank_by(candles_by_symbol: dict[str, list[dict]],
             months: int, skip: int = 0) -> list[tuple[str, Decimal]]:
    """Symbols with a computable return over the window, best first."""
    scored = []
    for sym in config.UNIVERSE:
        value = momentum.total_return(
            candles_by_symbol.get(sym, []), months, skip,
            config.DIVIDEND_YIELD.get(sym, Decimal("0")),
        )
        if value is not None:
            scored.append((sym, value))
    return sorted(scored, key=lambda x: x[1], reverse=True)


def _above_hurdle(ranked: list[tuple[str, Decimal]],
                  hurdle: Decimal) -> list[str]:
    """Apply absolute momentum, then take the top N."""
    eligible = [
        sym for sym, value in ranked
        if value > hurdle and sym != config.CASH_SYMBOL
    ]
    return eligible[:config.TOP_N]


def _equal(symbols: list[str]) -> dict[str, Decimal]:
    if not symbols:
        return {}
    weight = Decimal("1") / config.TOP_N
    return {sym: weight for sym in symbols}


def _hurdle_for(ranked: list[tuple[str, Decimal]]) -> Decimal:
    cash = next((v for s, v in ranked if s == config.CASH_SYMBOL), None)
    return cash if cash is not None else Decimal("0")


def variant_single_lookback(candles_by_symbol: dict[str, list[dict]],
                            months: int, skip: int = 0) -> dict[str, Decimal]:
    ranked = _rank_by(candles_by_symbol, months, skip)
    return _equal(_above_hurdle(ranked, _hurdle_for(ranked)))


def variant_blended(candles_by_symbol: dict[str, list[dict]]
                    ) -> dict[str, Decimal]:
    """Average the rank across 3, 6 and 12 months.

    Averaging positions rather than returns keeps a single outsized number
    from dominating: a symbol up 300% over a year outranks everything on
    the twelve-month leg no matter how it has behaved since.
    """
    legs = [_rank_by(candles_by_symbol, m) for m in (3, 6, 12)]

    positions: dict[str, list[int]] = {}
    for leg in legs:
        for place, (sym, _) in enumerate(leg, 1):
            positions.setdefault(sym, []).append(place)

    # Only symbols with a full set of legs are comparable.
    complete = {s: p for s, p in positions.items() if len(p) == len(legs)}
    averaged = sorted(
        ((s, Decimal(sum(p)) / len(p)) for s, p in complete.items()),
        key=lambda x: x[1],
    )

    cash_rank = next((r for s, r in averaged if s == config.CASH_SYMBOL), None)
    hurdle = cash_rank if cash_rank is not None else Decimal("999")

    # Lower is better here, so the comparison flips.
    eligible = [
        s for s, rank in averaged
        if rank < hurdle and s != config.CASH_SYMBOL
    ]
    return _equal(eligible[:config.TOP_N])


def variant_inverse_vol(candles_by_symbol: dict[str, list[dict]],
                        selected: dict[str, Decimal]) -> dict[str, Decimal]:
    """The live selection, weighted by 1/volatility instead of equally.

    Equal weights give each name the same money, not the same risk.
    """
    vols = {}
    for sym in selected:
        vol = indicators.volatility(candles_by_symbol.get(sym, []))
        if vol and vol > 0:
            vols[sym] = vol

    if len(vols) != len(selected):
        return dict(selected)

    inverse = {s: Decimal("1") / v for s, v in vols.items()}
    total = sum(inverse.values())
    return {s: w / total for s, w in inverse.items()}


def variant_trend_filtered(candles_by_symbol: dict[str, list[dict]],
                           selected: dict[str, Decimal]) -> dict[str, Decimal]:
    """The live selection, minus anything trading below its 200-day average.

    Dropped names are not replaced - the freed weight stays in cash, since
    a weak tape is the wrong moment to concentrate.
    """
    kept = [
        sym for sym in selected
        if (indicators.above_moving_average(candles_by_symbol.get(sym, []))
            or Decimal("0")) > 0
    ]
    weight = Decimal("1") / config.TOP_N
    return {sym: weight for sym in kept}


def variants(candles_by_symbol: dict[str, list[dict]],
             signal: Signal) -> list[Variant]:
    """Every alternative allocation for today, for the record."""
    return [
        Variant("mom_6m", variant_single_lookback(candles_by_symbol, 6)),
        Variant("mom_12m_no_skip",
                variant_single_lookback(candles_by_symbol, 12, 0)),
        Variant("blended_rank", variant_blended(candles_by_symbol)),
        Variant("inverse_vol",
                variant_inverse_vol(candles_by_symbol, signal.weights)),
        Variant("trend_filtered",
                variant_trend_filtered(candles_by_symbol, signal.weights)),
    ]