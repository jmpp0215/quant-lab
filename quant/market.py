"""Market session helpers: KIS holiday calendar + the KST clock."""

from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# KRX continuous session. The 15:20-15:30 closing single-price auction is
# inside this window on purpose - executor.auction_imminent() guards that
# separately; current_session() only answers "is the market open at all".
REGULAR_OPEN = time(9, 0)
REGULAR_CLOSE = time(15, 30)

KR_TICK_SIZES = [
    (Decimal("1000"), Decimal("1")),
    (Decimal("2000"), Decimal("1")),
    (Decimal("5000"), Decimal("5")),
    (Decimal("10000"), Decimal("10")),
    (Decimal("20000"), Decimal("10")),
    (Decimal("50000"), Decimal("50")),
    (Decimal("100000"), Decimal("100")),
    (Decimal("200000"), Decimal("100")),
    (Decimal("500000"), Decimal("500")),
]
ETF_TICK = Decimal("5")


def is_business_day(holiday: dict) -> bool:
    """True when KRX opens today.

    `holiday` is a KisClient.holidays() response: output[0] is the queried
    date itself, and opnd_yn ('Y'/'N') is whether the market opens then -
    a weekend or public holiday is 'N'. Distinguishes a holiday from merely
    being outside session hours: only the former means there is no candle
    for today at all.
    """
    rows = holiday.get("output") or []
    return bool(rows) and rows[0].get("opnd_yn") == "Y"


def current_session(now: datetime | None = None) -> str | None:
    """Which session the KST clock is in: "preMarket" before the open,
    "regularMarket" during continuous trading, "afterMarket" once the
    close has passed. Callers check this only after is_business_day().
    """
    now = (now or datetime.now(KST)).astimezone(KST).time()
    if now < REGULAR_OPEN:
        return "preMarket"
    if now < REGULAR_CLOSE:
        return "regularMarket"
    return "afterMarket"


def kr_tick_size(price: str | Decimal, is_etf: bool = False) -> Decimal:
    """Return the KRX tick size for a given price level."""
    if is_etf:
        return ETF_TICK

    p = Decimal(price)
    for threshold, tick in KR_TICK_SIZES:
        if p < threshold:
            return tick
    return Decimal("1000")


def is_valid_kr_price(price: str | Decimal, is_etf: bool = False) -> bool:
    p = Decimal(price)
    return p % kr_tick_size(p, is_etf) == 0


def round_to_tick(price: Decimal, is_etf: bool = False) -> Decimal:
    tick = kr_tick_size(price, is_etf)
    return (price // tick) * tick
