"""Momentum calculations for the dual momentum strategy."""

import logging
from datetime import date, timedelta
from decimal import Decimal

log = logging.getLogger(__name__)

DAYS_PER_MONTH = Decimal("30.44")


def _date_of(candle: dict) -> date:
    return date.fromisoformat(candle["timestamp"][:10])


def _price_at_or_before(candles: list[dict], target: date) -> Decimal | None:
    """Closing price on the last trading day at or before `target`.

    Indexing by position would assume every symbol shares one trading
    calendar, which breaks across markets and after any gap in a symbol's
    history. Candles are newest-first, so the first match is the closest.
    """
    for candle in candles:
        if _date_of(candle) <= target:
            return Decimal(candle["closePrice"])
    return None


def price_return(candles: list[dict], months: int,
                 skip_months: int = 0) -> Decimal | None:
    """Return over `months`, ending `skip_months` before the latest candle.

    Returns None when history does not reach far enough back, so the caller
    can exclude the symbol rather than rank it on a partial window.
    """
    if not candles:
        return None

    anchor = _date_of(candles[0])
    recent_target = anchor - timedelta(days=int(skip_months * DAYS_PER_MONTH))
    past_target = anchor - timedelta(days=int(months * DAYS_PER_MONTH))

    if _date_of(candles[-1]) > past_target:
        return None

    recent = _price_at_or_before(candles, recent_target)
    past = _price_at_or_before(candles, past_target)

    if recent is None or past is None or past == 0:
        return None

    return (recent - past) / past


def total_return(candles: list[dict], months: int,
                 skip_months: int = 0,
                 annual_yield: Decimal = Decimal("0")) -> Decimal | None:
    """Price return plus an approximation of distributions.

    Toss candles are price-only, so a high-yield symbol ranks unfairly low:
    the ex-dividend drop shows up in the price while the payout does not.
    Spreading the annual yield evenly over the holding window is rough, but
    far closer than ignoring distributions entirely.
    """
    base = price_return(candles, months, skip_months)
    if base is None:
        return None

    holding_months = Decimal(months - skip_months)
    return base + annual_yield * holding_months / Decimal(12)


def trailing_yield(candles: list[dict], events: list[dict],
                   months: int = 12) -> Decimal:
    """Real distributions paid in the trailing `months`, as a fraction of
    the latest price - a live-computed stand-in for a hand-maintained
    annual yield estimate, meant to be fed straight into total_return()'s
    `annual_yield` parameter.

    events: [{"record_date": "YYYY-MM-DD", "amount": Decimal}, ...] - the
    payout history a caller has already fetched/cached (never fetched here;
    this stays a pure function like the rest of the module).

    Anchored to the candles' own latest date, like price_return - never
    datetime.now() - so a caller can hand this pre-sliced historical
    candles/events during a backtest and get the answer that date would
    actually have seen, with no look-ahead.

    The boundary uses each event's record_date, not its payment date
    (divi_pay_dt can lag record_date by weeks) - the amount may not have
    actually been public knowledge quite this early, a minor imprecision
    left as-is given how few historical decisions this ever affects.
    """
    if not candles:
        return Decimal("0")

    anchor = _date_of(candles[0])
    price = Decimal(candles[0]["closePrice"])
    if price <= 0:
        return Decimal("0")

    since = anchor - timedelta(days=int(months * DAYS_PER_MONTH))
    total = sum(
        (e["amount"] for e in events
         if since < date.fromisoformat(e["record_date"]) <= anchor),
        Decimal("0"),
    )
    return total / price