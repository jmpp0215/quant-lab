"""Momentum calculations for the dual momentum strategy."""

import logging
from decimal import Decimal

log = logging.getLogger(__name__)

TRADING_DAYS_PER_MONTH = 21


def total_return(candles: list[dict], months: int,
                 skip_months: int = 0) -> Decimal | None:
    """Total return over `months`, optionally skipping the most recent
    `skip_months` to avoid short-term reversal (the standard 12-1 approach).
    """
    start = skip_months * TRADING_DAYS_PER_MONTH
    end = months * TRADING_DAYS_PER_MONTH

    if len(candles) <= end:
        return None

    recent = Decimal(candles[start]["closePrice"])
    past = Decimal(candles[end]["closePrice"])
    if past == 0:
        return None

    return (recent - past) / past
