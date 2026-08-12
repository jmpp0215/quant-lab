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

SESSION_NAMES = ("preMarket", "regularMarket", "afterMarket", "dayMarket")


def _sessions(calendar: dict) -> dict:
    """Return today's session map, flattening the KR 'integrated' wrapper."""
    today = calendar["result"]["today"]
    return today.get("integrated", today)

def kr_tick_size(price: str | Decimal) -> Decimal:
    """Return the KRX tick size for a given price level."""
    p = Decimal(price)
    for threshold, tick in KR_TICK_SIZES:
        if p < threshold:
            return tick
    return Decimal("1000")


def is_valid_kr_price(price: str | Decimal) -> bool:
    """Check that a price sits exactly on a valid KRX tick."""
    p = Decimal(price)
    return p % kr_tick_size(p) == 0


def round_to_tick(price: Decimal) -> Decimal:
    """Snap a price down to the nearest valid KRX tick."""
    tick = kr_tick_size(price)
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