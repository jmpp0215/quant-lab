"""Guards against unadjusted-price artifacts in pead_price_raw.

pead_price_raw stores raw KRX closes (quant/pead/scripts/backfill_prices.py,
refresh_prices.py) with no retroactive adjustment for corporate actions that
re-base a stock's share price: 무상감자/유상감자 (capital reduction),
액면병합/분할 (par value consolidation/split), 무상증자 (bonus issue). A
day-over-day return computed across such a date is not a real return - e.g.
a 15:1 reverse split reads as a ~+1,450% day even though a continuing holder
saw roughly no change in value. This is not specific to any sector: it has
been observed in pharma, brokerage, construction, and auto-parts names alike
(see RESEARCH_LOG.md section 15).

DEFAULT_ANOMALY_THRESHOLD of 50% is a heuristic, not a precise boundary -
callers that want to check sensitivity should sweep the threshold param.
"""
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEFAULT_ANOMALY_THRESHOLD = 0.5


@dataclass
class PriceAnomaly:
    symbol: str
    prev_date: str
    date: str
    ret: float


def safe_return(p_prev: float | None, p_curr: float | None, threshold: float = DEFAULT_ANOMALY_THRESHOLD) -> tuple[float | None, bool]:
    """Day-over-day return, or (None, True) if it looks like an unadjusted
    corporate-action artifact rather than a real market move.

    Returns (return_or_None, flagged). `flagged` is True only when both prices
    are present but the implied move exceeds `threshold` in absolute value -
    missing/zero prices return (None, False), matching the pre-existing
    "trading halt -> no return" convention used by callers.
    """
    if not p_prev or not p_curr or p_prev <= 0:
        return None, False
    ret = (p_curr - p_prev) / p_prev
    if abs(ret) > threshold:
        return None, True
    return ret, False


def find_anomalies(
    symbol: str,
    price_series: dict[str, float],
    threshold: float = DEFAULT_ANOMALY_THRESHOLD,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[PriceAnomaly]:
    """Every day-over-day jump in `price_series` exceeding `threshold`.

    `price_series` is {date(YYYYMMDD): close}. When start_date/end_date are
    given, only anomalies with `date` in [start_date, end_date] are returned,
    but the previous-day comparison still uses whatever date precedes it in
    the full series (so a window boundary can't hide an adjacent anomaly).
    """
    dates = sorted(price_series)
    anomalies = []
    for prev_date, date in zip(dates, dates[1:], strict=False):
        if start_date and date < start_date:
            continue
        if end_date and date > end_date:
            continue
        ret, flagged = safe_return(price_series[prev_date], price_series[date], threshold)
        if flagged:
            raw_ret = (price_series[date] - price_series[prev_date]) / price_series[prev_date]
            anomalies.append(PriceAnomaly(symbol, prev_date, date, raw_ret))
    return anomalies
