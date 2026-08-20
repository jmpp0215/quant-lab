"""Market session helpers based on the Toss market calendar."""

from datetime import datetime, timezone
from decimal import Decimal

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


SESSION_NAMES = ("preMarket", "regularMarket", "afterMarket", "dayMarket")


def _sessions(calendar: dict) -> dict:
    """Today's session map, flattening the KR 'integrated' wrapper.

    A holiday returns integrated: null rather than omitting the key, so
    the fallback has to handle None as well as a missing key.
    """
    today = calendar["result"]["today"]
    if "integrated" in today:
        return today["integrated"] or {}
    return today


def is_business_day(calendar: dict) -> bool:
    """True when the market trades today.

    Distinguishes a holiday from merely being outside session hours: both
    give current_session() == None, but only one means there will be no
    candle for today at all.
    """
    return bool(_sessions(calendar))

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

def to_decimal(value: str | None) -> Decimal | None:
    """Convert an API numeric string to Decimal, preserving exactness."""
    return None if value is None else Decimal(value)


def current_session(calendar: dict, now: datetime | None = None) -> str | None:
    """Return the name of the session we are currently in, or None if closed."""
    sessions = _sessions(calendar)
    now = now or datetime.now(timezone.utc)

    for name in SESSION_NAMES:
        window = sessions.get(name)
        if not window:
            continue

        start = datetime.fromisoformat(window["startTime"])
        end = datetime.fromisoformat(window["endTime"])

        if start <= now < end:
            return name

    return None


def seconds_until(calendar: dict, session: str,
                  now: datetime | None = None) -> float:
    """Seconds remaining until the given session starts. Negative if passed."""
    window = _sessions(calendar)[session]
    start = datetime.fromisoformat(window["startTime"])
    now = now or datetime.now(timezone.utc)
    return (start - now).total_seconds()