"""Observational indicators.

Computed and recorded every run, but deliberately not used to select
positions. The point is to accumulate a parallel record: three months
from now the signal log can answer "would volatility weighting have
picked differently?" without having traded on it first.
"""

import logging
from decimal import Decimal, InvalidOperation

from quant import momentum

log = logging.getLogger(__name__)

VOL_WINDOW = 60
MA_WINDOW = 200
HIGH_WINDOW = 252

# Trading days per year, for annualising daily volatility.
ANNUALISATION = Decimal("252").sqrt()


def _closes(candles: list[dict], n: int) -> list[Decimal]:
    return [Decimal(c["closePrice"]) for c in candles[:n]]


def volatility(candles: list[dict], window: int = VOL_WINDOW) -> Decimal | None:
    """Annualised standard deviation of daily returns.

    A symbol at the top of the momentum ranking with twice the volatility
    of its peers contributes far more risk than an equal weight implies.
    """
    if len(candles) <= window:
        return None

    closes = _closes(candles, window + 1)
    returns = [
        (closes[i] - closes[i + 1]) / closes[i + 1]
        for i in range(window)
        if closes[i + 1] != 0
    ]
    if len(returns) < 2:
        return None

    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)

    try:
        return variance.sqrt() * ANNUALISATION
    except InvalidOperation:
        return None


def above_moving_average(candles: list[dict],
                         window: int = MA_WINDOW) -> Decimal | None:
    """Latest close relative to its moving average, as a fraction.

    Positive means the symbol trades above trend. Used to judge whether a
    trend filter would have excluded anything the ranking selected.
    """
    if len(candles) < window:
        return None

    closes = _closes(candles, window)
    ma = sum(closes) / len(closes)
    if ma == 0:
        return None

    return (closes[0] - ma) / ma


def drawdown_from_high(candles: list[dict],
                       window: int = HIGH_WINDOW) -> Decimal | None:
    """Latest close relative to the highest close in the window.

    Zero at a new high, negative otherwise. Momentum crashes tend to start
    from names sitting at extended highs.
    """
    if not candles:
        return None

    closes = _closes(candles, window)
    peak = max(closes)
    if peak == 0:
        return None

    return (closes[0] - peak) / peak


def compute_all(candles: list[dict],
                annual_yield: Decimal = Decimal("0")) -> dict[str, Decimal | None]:
    """Every observational indicator for one symbol, keyed by name."""
    return {
        "mom_3m": momentum.total_return(candles, 3, 0, annual_yield),
        "mom_6m": momentum.total_return(candles, 6, 0, annual_yield),
        "mom_12m": momentum.total_return(candles, 12, 0, annual_yield),
        "mom_1m": momentum.total_return(candles, 1, 0, annual_yield),
        "vol_60d": volatility(candles),
        "above_ma200": above_moving_average(candles),
        "drawdown_52w": drawdown_from_high(candles),
    }