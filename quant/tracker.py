"""Whole-portfolio figures for the asset tracker (pure).

Everything here works across accounts: per-account returns already live in
quant/returns.py, and the tracker reuses those as they are. What it adds is
one growth index for the combined portfolio (so drawdown and volatility
aren't distorted by deposits or by an account whose snapshots started
later), holdings weights, and a goal projection.
"""

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

TRADING_DAYS = 252


def growth_index(snapshots: dict[str, list[tuple[str, Decimal]]],
                 deposits: dict[str, list[tuple[str, Decimal]]]
                 ) -> list[tuple[str, Decimal]]:
    """Combined time-weighted growth index, 1 on the first snapshot date.

    snapshots: account -> [(date, total)]; deposits: account -> [(date,
    amount)]. An account's gain on a date counts only when it has a
    snapshot that day (a missed day shows up as a bigger move on its next
    snapshot), and its value is carried forward in between so the
    denominator stays the whole portfolio. An account's first snapshot is
    not a gain - like a deposit, it only enlarges the base from then on.
    Deposits are netted out on the account's first snapshot at or after
    the deposit date; ones before its first snapshot are already in it.
    """
    by_account = {a: sorted(s) for a, s in snapshots.items() if s}
    dates = sorted({d for s in by_account.values() for d, _ in s})
    if not dates:
        return []

    last: dict[str, tuple[str, Decimal]] = {}   # account -> latest (date, value)
    index = Decimal("1")
    out = []
    for d in dates:
        gain, base = Decimal("0"), Decimal("0")
        for account, series in by_account.items():
            today = next((v for sd, v in series if sd == d), None)
            prev = last.get(account)
            if prev is not None:
                base += prev[1]
                if today is not None:
                    moved = sum((a for dd, a in deposits.get(account, [])
                                 if prev[0] < dd <= d), Decimal("0"))
                    gain += today - moved - prev[1]
            if today is not None:
                last[account] = (d, today)
        if base:
            index *= 1 + gain / base
        out.append((d, index))
    return out


def drawdown_series(index: list[tuple[str, Decimal]]) -> list[tuple[str, Decimal]]:
    """(date, drawdown from the running peak), 0 at a new high, negative below."""
    out, peak = [], None
    for d, v in index:
        peak = v if peak is None or v > peak else peak
        out.append((d, v / peak - 1))
    return out


def max_drawdown(index: list[tuple[str, Decimal]]) -> Decimal | None:
    dd = drawdown_series(index)
    return min(v for _, v in dd) if dd else None


def volatility(index: list[tuple[str, Decimal]]) -> Decimal | None:
    """Annualised sample stdev of snapshot-to-snapshot returns.

    None with fewer than two returns. Snapshots are one per trading day,
    so each return is treated as daily even across a missed day.
    """
    rets = [b / a - 1 for (_, a), (_, b) in zip(index, index[1:], strict=False)]
    if len(rets) < 2:
        return None
    mean = sum(rets, Decimal("0")) / len(rets)
    var = sum(((r - mean) ** 2 for r in rets), Decimal("0")) / (len(rets) - 1)
    return var.sqrt() * Decimal(TRADING_DAYS).sqrt()


def holdings(latest: list[dict]) -> list[tuple[str, Decimal]]:
    """Each holding's KRW value across accounts, largest first, cash as "현금".

    latest: one row per account - {"total", "cash", "positions"} with
    Decimal total/cash and positions as storage keeps them. A position
    priced in another currency (overseas, "currency": "USD") is converted
    at the rate implied by that account's own snapshot: whatever of the
    KRW total isn't cash or KRW positions is the foreign positions' value.
    No exchange rate is stored, and the broker's total is the trusted
    figure (see kis_client.snapshot_overseas).
    """
    values: dict[str, Decimal] = {}
    for row in latest:
        krw, foreign = [], []
        for p in row["positions"]:
            v = Decimal(p["price"]) * p["qty"]
            (foreign if p.get("currency", "KRW") != "KRW" else krw).append((p, v))
        rate = implied_rate(row)
        if rate is not None:
            krw += [(p, v * rate) for p, v in foreign]
        for p, v in krw:
            name = p.get("name") or p["symbol"]
            values[name] = values.get(name, Decimal("0")) + v
        values["현금"] = values.get("현금", Decimal("0")) + row["cash"]
    return sorted(values.items(), key=lambda kv: kv[1], reverse=True)


def implied_rate(row: dict) -> Decimal | None:
    """KRW per unit of the foreign positions' currency, as the snapshot
    values them: whatever of the KRW total isn't cash or KRW positions,
    over the foreign positions' own-currency value. None without any."""
    krw = foreign = Decimal("0")
    for p in row["positions"]:
        v = Decimal(p["price"]) * p["qty"]
        if p.get("currency", "KRW") == "KRW":
            krw += v
        else:
            foreign += v
    return (row["total"] - row["cash"] - krw) / foreign if foreign else None


@dataclass(frozen=True)
class FxSplit:
    """A foreign holding's KRW gain since purchase, split in two.

    price_effect: the own-currency gain, at the average rate paid.
    fx_effect: the rate move since purchase, on today's own-currency value.
    They sum to value_krw - cost_krw exactly.
    """
    name: str
    cost: Decimal          # own currency
    cost_krw: Decimal      # KRW actually paid
    value: Decimal         # own currency, now
    value_krw: Decimal
    buy_rate: Decimal
    price_effect: Decimal
    fx_effect: Decimal


def fx_split(positions: list[dict], rate: Decimal) -> list[FxSplit]:
    """FxSplit for each foreign position that carries cost and cost_krw
    (kis_client.snapshot_overseas), at today's rate."""
    out = []
    for p in positions:
        if p.get("currency", "KRW") == "KRW" or not p.get("cost") or not p.get("cost_krw"):
            continue
        cost, cost_krw = Decimal(p["cost"]), Decimal(p["cost_krw"])
        value = Decimal(p["price"]) * p["qty"]
        buy_rate = cost_krw / cost
        out.append(FxSplit(
            name=p.get("name") or p["symbol"], cost=cost, cost_krw=cost_krw,
            value=value, value_krw=value * rate, buy_rate=buy_rate,
            price_effect=(value - cost) * buy_rate,
            fx_effect=value * (rate - buy_rate)))
    return out


def hhi(weights: list[Decimal]) -> Decimal:
    """Herfindahl index of weights summing to 1: 1/N if equal, 1 if all in one."""
    return sum((w * w for w in weights), Decimal("0"))


def months_to_goal(current: Decimal, target: Decimal, monthly: Decimal,
                   annual_return: Decimal, limit: int = 600) -> int | None:
    """Months until current, growing at annual_return and adding monthly at
    each month end, reaches target. 0 if already there; None if not within
    limit months (e.g. no contribution and a non-positive return)."""
    rate = Decimal(str(math.pow(1 + float(annual_return), 1 / 12) - 1))
    value = current
    for month in range(limit + 1):
        if value >= target:
            return month
        value = value * (1 + rate) + monthly
    return None


def month_after(d: date, months: int) -> str:
    """"YYYY-MM" of the month `months` after d's month."""
    y, m = divmod(d.month - 1 + months, 12)
    return f"{d.year + y:04d}-{m + 1:02d}"
