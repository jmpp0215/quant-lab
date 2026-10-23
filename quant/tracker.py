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

from quant import allocation, momentum

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


def contributions(snapshots: dict[str, list[dict]],
                  deposits: dict[str, list[tuple[str, Decimal]]],
                  dividends: dict[str, list[tuple[str, Decimal]]],
                  start: str, end: str) -> list[dict]:
    """Where an account's gain came from, between start and end inclusive.

    snapshots: account -> [{"date", "total", "cash", "positions"}];
    deposits: recorded transfers; dividends: cash changes the transfer
    detector labelled dividends (date, KRW). Every snapshot step whose
    later date falls in [start, end] is split into:

    - "stock": each position held over the step, qty x its KRW price
      change - for a foreign position the own-currency move at the old
      rate (kind "stock") and the rate move on the new value (kind "fx");
    - "fx": also the rate move on foreign cash;
    - "dividend": as given;
    - "other": what's left - fills away from the close, fees, intraday
      moves on positions bought in the step.

    Rows: {"account", "name", "kind", "amount"}, merged by (account, name,
    kind). They sum exactly to the change in total value less transfers.
    """
    merged: dict[tuple[str, str, str], Decimal] = {}

    def add(account, name, kind, amount):
        key = (account, name, kind)
        merged[key] = merged.get(key, Decimal("0")) + amount

    for account, series in snapshots.items():
        series = sorted(series, key=lambda s: s["date"])
        for prev, cur in zip(series, series[1:], strict=False):
            if not start <= cur["date"] <= end:
                continue
            moved = sum((a for d, a in deposits.get(account, [])
                         if prev["date"] < d <= cur["date"]), Decimal("0"))
            gain = cur["total"] - prev["total"] - moved
            explained = Decimal("0")
            rate_prev, rate_cur = implied_rate(prev), implied_rate(cur)
            now = {p["symbol"]: p for p in cur["positions"]}
            for p in prev["positions"]:
                c = now.get(p["symbol"])
                if c is None:
                    continue   # sold in the step: its proceeds land in "other"
                name = p.get("name") or p["symbol"]
                qty = Decimal(p["qty"])
                p0, p1 = Decimal(p["price"]), Decimal(c["price"])
                if p.get("currency", "KRW") != "KRW" and rate_prev and rate_cur:
                    price_part = qty * (p1 - p0) * rate_prev
                    fx_part = qty * p1 * (rate_cur - rate_prev)
                    add(account, name, "stock", price_part)
                    add(account, "환율", "fx", fx_part)
                    explained += price_part + fx_part
                else:
                    add(account, name, "stock", qty * (p1 - p0))
                    explained += qty * (p1 - p0)
            if rate_prev and rate_cur and any(
                    p.get("currency", "KRW") != "KRW" for p in prev["positions"]):
                cash_fx = prev["cash"] / rate_prev * (rate_cur - rate_prev)
                add(account, "환율", "fx", cash_fx)
                explained += cash_fx
            paid = sum((a for d, a in dividends.get(account, [])
                        if prev["date"] < d <= cur["date"]), Decimal("0"))
            if paid:
                add(account, "배당", "dividend", paid)
            add(account, "기타", "other", gain - explained - paid)
    return [{"account": a, "name": n, "kind": k, "amount": v}
            for (a, n, k), v in merged.items() if v]


OVERSEAS_GAINS_DEDUCTION = Decimal("2500000")   # KRW per person per year
OVERSEAS_GAINS_RATE = Decimal("0.22")           # 20% + 2% local income tax


def overseas_gains_tax(rows: list[dict]) -> dict:
    """Estimated tax on a year's realized overseas gains.

    rows: kis_client.overseas_realized output (USD pnl and the rate KIS
    converts at). Gains and losses net within the year; the 2.5M deduction
    applies to the net. The KRW figure converts each sell's P&L at that
    sell day's rate, as KIS does - the tax law converts the cost at the
    buy-date rate instead, so this is an estimate, not the filing figure.
    """
    pnl_krw = sum((Decimal(r["pnl"]) * Decimal(r["rate"]) for r in rows), Decimal("0"))
    taxable = max(pnl_krw - OVERSEAS_GAINS_DEDUCTION, Decimal("0"))
    return {"pnl_usd": sum((Decimal(r["pnl"]) for r in rows), Decimal("0")),
            "pnl_krw": pnl_krw, "deduction": OVERSEAS_GAINS_DEDUCTION,
            "taxable": taxable, "tax": taxable * OVERSEAS_GAINS_RATE}


FAN_QUANTILES = (5, 25, 50, 75, 95)


def rolling_fan(values: list[float], horizon: int, min_paths: int = 30,
                cost: float = 0.0, cost_every: int = 0) -> dict:
    """Distribution of cumulative return k trading days after every start.

    values: one strategy's value per trading day, oldest first. Every day
    is a start, so paths overlap - quantiles of overlapping windows, not
    independent samples. cost (a fraction, e.g. 0.004) is taken once at
    the start and again every cost_every days, for a backtest that ran
    without trading costs. Stops at horizon or once fewer than min_paths
    windows reach that far. Returns {"days", "p5".."p95", "n"}.
    """
    out = {"days": [], "n": [], **{f"p{q}": [] for q in FAN_QUANTILES}}
    for k in range(horizon + 1):
        paths = len(values) - k
        if paths < min_paths:
            break
        charges = 1 + (k // cost_every if cost_every else 0)
        keep = (1 - cost) ** charges if cost else 1.0
        rets = sorted(values[i + k] / values[i] * keep - 1 for i in range(paths))
        out["days"].append(k)
        out["n"].append(paths)
        for q in FAN_QUANTILES:
            pos = q / 100 * (paths - 1)
            lo = int(pos)
            hi = min(lo + 1, paths - 1)
            out[f"p{q}"].append(rets[lo] + (rets[hi] - rets[lo]) * (pos - lo))
    return out


def fan_percentile(fan: dict, k: int, value: float) -> float | None:
    """Roughly where `value` sits at day k: interpolated between the stored
    quantiles, clamped to 0-100 outside the 5-95 band. None past the fan."""
    if k not in fan["days"]:
        return None
    i = fan["days"].index(k)
    pts = [(q, fan[f"p{q}"][i]) for q in FAN_QUANTILES]
    if value <= pts[0][1]:
        return 0.0 if value < pts[0][1] else float(pts[0][0])
    if value >= pts[-1][1]:
        return 100.0 if value > pts[-1][1] else float(pts[-1][0])
    for (q0, v0), (q1, v1) in zip(pts, pts[1:], strict=False):
        if v0 <= value <= v1:
            return q0 + (q1 - q0) * ((value - v0) / (v1 - v0) if v1 > v0 else 0)
    return None


def parse_prediction(text: str) -> dict:
    """One journal/predictions/*.md entry (the `prediction` skill's
    format): frontmatter fields, the title, and the **주장:** line.
    Inline "# ..." comments in frontmatter values are dropped."""
    meta, body = {}, text
    if text.startswith("---"):
        head, _, body = text[3:].partition("\n---")
        for line in head.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                meta[key.strip()] = value.split("#", 1)[0].strip() or None
    title = next((ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), None)
    claim = next((ln.split("**주장:**", 1)[1].strip() for ln in body.splitlines()
                  if "**주장:**" in ln), None)
    confidence = meta.get("confidence")
    return meta | {"title": title, "claim": claim,
                   "confidence": int(confidence) if confidence and confidence.isdigit() else None,
                   "scored": meta.get("scored") == "true",
                   "after_the_fact": meta.get("after_the_fact") == "true"}


def prediction_stats(entries: list[dict]) -> dict:
    """Hit rate over scored, resolved entries (partial counts half, void is
    left out) and the same per 10-point confidence bucket."""
    done = [e for e in entries if e["scored"] and e.get("status") == "resolved"
            and e.get("outcome") in ("hit", "miss", "partial")]
    score = {"hit": 1.0, "partial": 0.5, "miss": 0.0}
    buckets: dict[int, list[float]] = {}
    for e in done:
        if e["confidence"] is not None:
            buckets.setdefault(min(e["confidence"] // 10 * 10, 90), []).append(score[e["outcome"]])
    return {"resolved": len(done),
            "hit_rate": sum(score[e["outcome"]] for e in done) / len(done) if done else None,
            "by_confidence": [{"bucket": b, "n": len(v), "hit_rate": sum(v) / len(v)}
                              for b, v in sorted(buckets.items())]}


def isa_paid(paid_total: Decimal, paid_this_year: Decimal | None, as_of: str,
             deposits: list[tuple[str, Decimal]], today: date) -> dict:
    """Paid-in as the user entered it on `as_of`, plus ISA deposits recorded
    after that date - so a contribution is entered once, as a cashflow.

    paid_this_year is the entered year-to-date figure; in a later year it
    no longer applies and only that year's deposits count.
    """
    later = [(d, a) for d, a in deposits if d > as_of]
    added = sum((a for _, a in later), Decimal("0"))
    year = str(today.year)
    this_year = sum((a for d, a in later if d.startswith(year)), Decimal("0"))
    if as_of[:4] == year:
        this_year = paid_this_year + this_year if paid_this_year is not None else None
    return {"total": paid_total + added, "this_year": this_year, "added": added}


def benchmark_series(candles: list[dict], dates: list[str]) -> list[Decimal | None]:
    """Cumulative return of a benchmark on each date, from dates[0]'s close.

    candles newest-first (candles.py's cache). Each date takes the last
    close at or before it - the same date-anchored lookup the momentum
    signal uses, since a benchmark's calendar needn't match the account's.
    None for every date if there is no close at or before the first.
    """
    if not dates:
        return []
    base = momentum._price_at_or_before(candles, date.fromisoformat(dates[0]))
    if not base:
        return [None] * len(dates)
    out = []
    for d in dates:
        p = momentum._price_at_or_before(candles, date.fromisoformat(d))
        out.append(p / base - 1 if p is not None else None)
    return out


# What each ETF the strategies trade or hold actually exposes the account to:
# (asset class, region, currency). "(H)" funds are currency-hedged, so their
# currency exposure is KRW. Anything not listed falls back to the position's
# own currency: a foreign-priced position is a foreign stock, a KRW one a
# Korean stock.
EXPOSURE = {
    "102110": ("주식", "한국", "KRW"), "466920": ("주식", "한국", "KRW"),
    "091170": ("주식", "한국", "KRW"), "487240": ("주식", "한국", "KRW"),
    "0074K0": ("주식", "한국", "KRW"),
    "133690": ("주식", "미국", "USD"), "0167Z0": ("주식", "미국", "USD"),
    "379790": ("주식", "유럽", "KRW"), "195930": ("주식", "유럽", "KRW"),
    "371160": ("주식", "중국·홍콩", "기타 외화"), "241180": ("주식", "일본", "기타 외화"),
    "484790": ("채권", "미국", "KRW"),
    "497880": ("현금성", "한국", "KRW"), "459580": ("현금성", "한국", "KRW"),
}
FOREIGN_REGION = {"USD": "미국"}


def exposures(latest: list[dict]) -> list[dict]:
    """Every holding and cash balance across accounts with its KRW value and
    (asset, region, currency) exposure.

    latest: {"account", "total", "cash", "positions"} per account. Foreign
    positions are valued at the account's implied rate (as holdings() does);
    an account holding foreign positions holds its cash in that currency.
    """
    out = []
    for row in latest:
        rate = implied_rate(row)
        foreign_ccy = None
        for p in row["positions"]:
            ccy = p.get("currency", "KRW")
            value = Decimal(p["price"]) * p["qty"]
            if ccy != "KRW":
                foreign_ccy = ccy
                value = value * rate if rate else Decimal("0")
            asset, region, cur = EXPOSURE.get(
                p["symbol"], ("주식", FOREIGN_REGION.get(ccy, "기타") if ccy != "KRW" else "한국", ccy))
            out.append({"account": row["account"], "symbol": p["symbol"],
                        "name": p.get("name") or p["symbol"], "value": value,
                        "asset": asset, "region": region, "currency": cur})
        if row["cash"]:
            out.append({"account": row["account"], "symbol": "", "name": "현금",
                        "value": row["cash"], "asset": "현금",
                        "region": FOREIGN_REGION.get(foreign_ccy, "기타") if foreign_ccy else "한국",
                        "currency": foreign_ccy or "KRW"})
    return out


def breakdown(rows: list[dict], key: str) -> list[tuple[str, Decimal]]:
    """Share of total value by `key` ("asset", "region", "currency"), largest first."""
    total = sum((r["value"] for r in rows), Decimal("0"))
    acc: dict[str, Decimal] = {}
    for r in rows:
        acc[r[key]] = acc.get(r[key], Decimal("0")) + r["value"]
    return sorted(((k, v / total) for k, v in acc.items()), key=lambda kv: -kv[1]) if total else []


def account_risk(index: dict[str, list[tuple[str, Decimal]]],
                 values: dict[str, Decimal]) -> dict:
    """Per-account volatility, drawdown, correlation and share of portfolio risk.

    index: account -> its own deposit-neutral growth index (growth_index for
    that account alone); values: account -> current KRW value (the weights).
    Returns are taken between dates every account with variance has a
    snapshot on, so a missed day becomes one longer step for all of them.
    Accounts that never moved (cash only) are kept in the weights but left
    out of the covariance, adding nothing to risk.
    """
    moving = [a for a, s in index.items()
              if len({v for _, v in s}) > 1 and values.get(a)]
    common = sorted(set.intersection(*(set(d for d, _ in index[a]) for a in moving))) if moving else []
    rets = {}
    for a in moving:
        by_date = dict(index[a])
        series = [float(by_date[d]) for d in common]
        rets[a] = [b / x - 1 for x, b in zip(series, series[1:], strict=False)]
    total = sum((v for v in values.values()), Decimal("0"))
    out = {"days": max(len(common) - 1, 0), "accounts": {}, "corr": {}, "portfolio_vol": None}
    if len(common) < 3 or not total:
        return out
    cov = allocation.covariance_matrix(rets, moving)
    w = [float(values[a] / total) for a in moving]
    contrib = allocation.risk_contributions(w, cov)
    port = allocation.portfolio_volatility(w, cov)
    for i, a in enumerate(moving):
        out["accounts"][a] = {"vol": cov[i][i] ** 0.5, "weight": w[i],
                              "risk_share": contrib[i] / port if port else 0.0,
                              "mdd": float(max_drawdown(index[a]) or 0)}
        for j, b in enumerate(moving):
            if j > i:
                den = (cov[i][i] * cov[j][j]) ** 0.5
                out["corr"][f"{a}|{b}"] = cov[i][j] / den if den else None
    out["portfolio_vol"] = port
    return out


def days_to_exit(positions: list[dict], avg_value: dict[str, Decimal],
                 participation: Decimal = Decimal("0.10")) -> list[dict]:
    """Trading days to sell each position without exceeding `participation`
    of its average daily traded value, slowest first.

    positions: {"symbol", "name", "value"} in KRW; avg_value: symbol -> mean
    daily traded value (KRW). A symbol with no trading history gets None.
    """
    out = []
    for p in positions:
        adv = avg_value.get(p["symbol"])
        days = p["value"] / (adv * participation) if adv else None
        out.append({"symbol": p["symbol"], "name": p["name"], "value": p["value"],
                    "adv": adv, "days": days})
    return sorted(out, key=lambda r: -(r["days"] if r["days"] is not None else Decimal("1e9")))


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


# University-style 4.3 scale. Each rule maps a "lower is better" measure to
# a letter by the first limit it doesn't exceed; past the last limit it is
# the rule's floor grade. Thresholds are a judgment call, kept here in one
# place so they can be argued with.
GRADE_POINTS = {"A+": 4.3, "A": 4.0, "A-": 3.7, "B+": 3.3, "B": 3.0, "B-": 2.7,
                "C+": 2.3, "C": 2.0, "C-": 1.7, "D+": 1.3, "D": 1.0, "D-": 0.7, "F": 0.0}
RISK_RULES = (
    ("single", "최대 단일 종목 비중",
     ((0.10, "A+"), (0.15, "A"), (0.20, "B+"), (0.25, "B"), (0.30, "C+"), (0.40, "C"), (0.50, "D")), "F"),
    ("top5", "상위 5종목 비중",
     ((0.40, "A+"), (0.50, "A"), (0.60, "B+"), (0.70, "B"), (0.80, "C")), "D"),
    ("vol", "연환산 변동성",
     ((0.08, "A+"), (0.10, "A"), (0.12, "A-"), (0.15, "B+"), (0.18, "B"), (0.22, "C+"), (0.28, "C")), "D"),
    ("crisis", "과거 위기 최악 낙폭",
     ((0.10, "A+"), (0.15, "A"), (0.20, "A-"), (0.25, "B+"), (0.30, "B"), (0.35, "C+"), (0.40, "C"),
      (0.50, "D")), "F"),
    ("currency", "한 통화 최대 비중",
     ((0.50, "A+"), (0.60, "A"), (0.70, "B+"), (0.80, "B"), (0.90, "C")), "D"),
    ("liquidity", "전량 매도 최장 일수",
     ((1, "A+"), (3, "A"), (5, "B+"), (10, "B"), (20, "C")), "D"),
)


def letter(points: float) -> str:
    """The highest letter whose points `points` reaches."""
    return next(g for g, p in GRADE_POINTS.items() if points >= p - 1e-9)


def risk_grade(metrics: dict[str, float | None]) -> dict:
    """GPA (equal-weight mean of each available measure's points) and the
    per-measure grades. metrics: rule key -> value (crisis as a positive
    loss); None or missing measures are left out of the mean."""
    items = []
    for key, label, steps, floor in RISK_RULES:
        v = metrics.get(key)
        if v is None:
            continue
        g = next((g for limit, g in steps if v <= limit), floor)
        items.append({"key": key, "label": label, "value": v, "grade": g, "points": GRADE_POINTS[g]})
    if not items:
        return {}
    gpa = sum(i["points"] for i in items) / len(items)
    return {"gpa": gpa, "letter": letter(gpa), "max": 4.3, "items": items}
