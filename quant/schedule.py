"""Upcoming rebalances, dividends and earnings for the asset tracker (pure).

daily.py fetches the calendar, dividend announcements and earnings dates
and records what
these functions return; the tracker only reads the result. Nothing here
decides whether to trade - tranche.due_today and check_pbr_due stay the
authority on that; this only projects their rules forward to show dates.
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from quant import config
from quant.market import KST


def open_days(holiday_responses: list[dict]) -> list[str]:
    """KRX open days ("YYYY-MM-DD") from KisClient.holidays() responses."""
    days = set()
    for resp in holiday_responses:
        for row in resp.get("output") or []:
            if row.get("opnd_yn") == "Y":
                raw = row["bass_dt"]
                days.add(f"{raw[:4]}-{raw[4:6]}-{raw[6:]}")
    return sorted(days)


def tranche_dates(days: list[str], today: str,
                  done_this_month: set[int]) -> list[tuple[str, int]]:
    """(date, tranche) for every scheduled tranche from today on.

    days must run unbroken from the start of today's month, so a month's
    nth entry is its nth trading day. A tranche this month that is past
    its day and not yet run is shown on the first open day from today -
    the catch-up tranche.due_today applies. Months cut off at the end of
    `days` only lose their later tranches.
    """
    out = []
    upcoming = [d for d in days if d >= today]
    for month in sorted({d[:7] for d in days if d[:7] >= today[:7]}):
        month_days = [d for d in days if d[:7] == month]
        for t in sorted(config.TRANCHES):
            if t >= len(month_days):
                continue
            day = month_days[t]
            if month == today[:7]:
                if t in done_this_month:
                    continue
                if day < today:
                    if not upcoming:
                        continue
                    day = upcoming[0]
            out.append((day, t))
    return sorted(out)


def trading_days_after(days: list[str], start: str, n: int) -> str | None:
    """The trading day n days after start, or None if start isn't in days
    or the calendar doesn't reach that far."""
    if start not in days:
        return None
    i = days.index(start) + n
    return days[i] if i < len(days) else None


def upcoming_dividends(events: list[dict], holdings: list[dict],
                       today: str) -> list[dict]:
    """Announced dividends on held symbols with a record date from today on.

    events: {"symbol", "record_date", "amount" (Decimal, 0 = not yet set),
    "pay_date" (or None)}. holdings: {"account", "symbol", "name", "qty"}.
    One row per (account, event); "expected" is qty x amount before tax,
    None while the amount isn't set.
    """
    by_symbol: dict[str, list[dict]] = {}
    for h in holdings:
        by_symbol.setdefault(h["symbol"], []).append(h)
    out = []
    for e in events:
        if e["record_date"] < today:
            continue
        for h in by_symbol.get(e["symbol"], []):
            amount = e["amount"] if e["amount"] > 0 else None
            out.append({
                "account": h["account"], "symbol": e["symbol"],
                "name": h["name"], "record_date": e["record_date"],
                "pay_date": e.get("pay_date"), "per_share": amount,
                "qty": h["qty"],
                "expected": amount * h["qty"] if amount is not None else None,
            })
    return sorted(out, key=lambda r: (r["record_date"], r["account"], r["symbol"]))



# Nasdaq's earnings-date reportText, e.g. "... is expected* to report
# earnings on  11/02/2026 after market close." "estimated" means Nasdaq's
# own guess from past report dates, not a date the vendor has.
_EARNINGS_RE = re.compile(
    r"is (expected\*?|estimated) to report earnings on\s+(\d{2})/(\d{2})/(\d{4})"
    r"(\s+after market close|\s+before market open)?")
NY = ZoneInfo("America/New_York")
# Report times are only given as before open / after close; these are the
# usual release hours, so the KST time shown is approximate.
_REPORT_HOUR = {"amc": 16, "bmo": 8}


def parse_earnings(report_text: str) -> dict | None:
    """{"us_date", "timing" ("amc"/"bmo"/None), "estimated"} from Nasdaq's
    reportText, or None when it has no upcoming date."""
    m = _EARNINGS_RE.search(report_text or "")
    if m is None:
        return None
    timing = m.group(5)
    return {"us_date": f"{m.group(4)}-{m.group(2)}-{m.group(3)}",
            "timing": None if timing is None
            else "amc" if "after" in timing else "bmo",
            "estimated": m.group(1) == "estimated"}


def earnings_kst(us_date: str, timing: str | None) -> tuple[str, str | None]:
    """(KST date, "HH:MM" or None) of a US report. An after-close report
    lands early the next morning in Korea; DST is taken from the date."""
    if timing is None:
        return us_date, None
    at = datetime.fromisoformat(us_date).replace(hour=_REPORT_HOUR[timing], tzinfo=NY)
    kst = at.astimezone(KST)
    return kst.date().isoformat(), kst.strftime("%H:%M")


def merge_earnings(cached: dict, fetched: dict, held: set[str],
                   today: str) -> dict:
    """Earnings dates by symbol for the held symbols: this run's fetch, or
    the cached date when the fetch has none and the KST date isn't past.

    Nasdaq drops a date around the day itself (MSFT and GOOGL came back
    empty on their 2026-10-28 report day), which is exactly when it matters.
    """
    out = {}
    for symbol in held:
        e = fetched.get(symbol) or cached.get(symbol)
        if e is not None and earnings_kst(e["us_date"], e["timing"])[0] >= today:
            out[symbol] = e
    return out
