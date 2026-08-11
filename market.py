"""Market session helpers based on the Toss market calendar."""

from datetime import datetime, timezone


def current_session(calendar: dict, now: datetime | None = None) -> str | None:
    """Return the name of the session we are currently in, or None if closed.

    The calendar returns ISO timestamps already converted to KST, so we can
    compare them directly without doing any timezone math ourselves.
    """
    today = calendar["result"]["today"]
    now = now or datetime.now(timezone.utc)

    for name in ("preMarket", "regularMarket", "afterMarket", "dayMarket"):
        window = today.get(name)
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
    window = calendar["result"]["today"][session]
    start = datetime.fromisoformat(window["startTime"])
    now = now or datetime.now(timezone.utc)
    return (start - now).total_seconds()