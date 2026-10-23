"""Today's holdings run through history (pure): past crises replayed on the
current portfolio, and the range of 1-month / 1-year returns the same
holdings would have had.

Every holding moves the way its own price did - or, when it wasn't listed
yet, the way a proxy index did - converted to KRW at that day's exchange
rate when the exposure is unhedged foreign currency. The joint move is the
real one (in 2008 the won fell while US stocks fell, which cushioned USD
holdings in KRW); nothing here is an assumed shock.

series: Yahoo ticker -> (sorted dates "YYYY-MM-DD", adjusted closes), as
price_history.py caches them. rows: tracker.exposures() rows. tickers:
holding symbol -> its own Yahoo ticker, or None when Yahoo has none.
"""

from bisect import bisect_right

CRISES = (
    ("2008 금융위기", "2008-09-01", "2009-03-09"),   # Lehman month -> S&P 500 low
    ("2020 코로나", "2020-02-19", "2020-03-23"),     # S&P 500 high -> low
    ("2022 금리 인상", "2022-01-03", "2022-10-14"),  # first trading day -> S&P 500 low
)
FX = "KRW=X"                       # KRW per USD
PROXIES = ("^KS11", "^KQ11", "^GSPC", "^STOXX50E", "TLT", FX)
REGION_PROXY = {("주식", "한국"): "^KS11", ("주식", "미국"): "^GSPC",
                ("주식", "유럽"): "^STOXX50E", ("채권", "미국"): "TLT"}
NON_USD = ("^KS11", "^KQ11", "^STOXX50E")


def _usd(ticker: str) -> bool:
    return not (ticker.endswith((".KS", ".KQ")) or ticker in NON_USD)


def _asof(s: tuple[list[str], list[float]], d: str) -> float | None:
    i = bisect_right(s[0], d)
    return s[1][i - 1] if i else None


def source(row: dict, tickers: dict, series: dict, start: str) -> tuple | None:
    """(ticker or None, convert_fx, proxied) for one exposure row, or None
    when nothing covers `start`. A None ticker means the value doesn't move
    (KRW cash, money-market ETFs); USD cash moves with the rate alone."""
    if row["asset"] == "현금":
        return (None, row["currency"] == "USD", False)
    if row["asset"] == "현금성":
        return (None, False, False)
    own = tickers.get(row["symbol"])
    if own in series and series[own][0][0] <= start:
        return (own, row["currency"] == "USD" and _usd(own), False)
    if row["currency"] == "KRW" and row["region"] == "한국" and row["asset"] == "주식":
        proxy = "^KQ11" if own and own.endswith(".KQ") else "^KS11"
    else:
        proxy = REGION_PROXY.get((row["asset"], row["region"]))
    if proxy not in series or series[proxy][0][0] > start:
        return None
    return (proxy, row["currency"] == "USD" and _usd(proxy), True)


def _multiplier(src: tuple, series: dict, d0: str):
    """Function date -> KRW value at that date relative to d0."""
    ticker, fx, _ = src
    base = _asof(series[ticker], d0) if ticker else 1.0
    fx0 = _asof(series[FX], d0) if fx else 1.0

    def m(d: str) -> float:
        p = _asof(series[ticker], d) / base if ticker else 1.0
        return p * (_asof(series[FX], d) / fx0 if fx else 1.0)
    return m


def _calendar(series: dict, used: set[str], start: str, end: str = "9999") -> list[str]:
    """Trading days of the holdings' own series (no FX-only days)."""
    return sorted({d for t in used for d in series[t][0] if start <= d <= end})


def replay(rows: list[dict], tickers: dict, series: dict, start: str, end: str) -> dict:
    """Hold today's positions from `start` to `end` without trading.

    Returns ret (at end), worst (lowest point vs start) and its date, the
    same per account, the three holdings that lost the most won, and which
    holdings were proxied or left out (share of value)."""
    total = sum(float(r["value"]) for r in rows)
    used, proxied, excluded, srcs = [], [], 0.0, []
    for r in rows:
        src = source(r, tickers, series, start)
        if src is None:
            excluded += float(r["value"])
            continue
        if src[2]:
            proxied.append(r["name"])
        srcs.append(src)
        used.append((r, _multiplier(src, series, start)))
    days = _calendar(series, {src[0] for src in srcs if src[0]}, start, end)
    if not days or not used:
        return {}

    def path(subset):
        v0 = sum(float(r["value"]) for r, _ in subset)
        return [sum(float(r["value"]) * m(d) for r, m in subset) / v0 - 1 for d in days]

    def summary(subset):
        p = path(subset)
        i = min(range(len(p)), key=p.__getitem__)
        return {"ret": p[-1], "worst": min(p[i], 0.0), "worst_date": days[i]}

    out = summary(used)
    out["accounts"] = {a: summary([u for u in used if u[0]["account"] == a])
                       for a in dict.fromkeys(r["account"] for r, _ in used)}
    loss: dict[str, float] = {}
    for r, m in used:
        if r["asset"] != "현금":
            loss[r["name"]] = loss.get(r["name"], 0.0) + float(r["value"]) * (m(days[-1]) - 1)
    out["losers"] = sorted(((n, v) for n, v in loss.items() if v < 0), key=lambda kv: kv[1])[:3]
    out["proxied"] = sorted(set(proxied))
    out["excluded_share"] = excluded / total if total else 0.0
    return out


def _quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def holding_range(rows: list[dict], tickers: dict, series: dict, start: str,
                  horizons: tuple[int, ...] = (21, 250)) -> dict:
    """Buy-and-hold returns of today's weights over every `k`-trading-day
    window since `start`: {k: {"p5", "p50", "p_below_20", "n"}} (p_below_20
    = share of 1-year windows at or below -20%), plus proxied names."""
    used, proxied, srcs = [], [], []
    for r in rows:
        src = source(r, tickers, series, start)
        if src is None:
            continue
        if src[2]:
            proxied.append(r["name"])
        srcs.append(src)
        used.append((float(r["value"]), _multiplier(src, series, start)))
    days = _calendar(series, {src[0] for src in srcs if src[0]}, start)
    if not used or not days:
        return {}
    v0 = sum(w for w, _ in used)
    weights = [w / v0 for w, _ in used]
    levels = [[m(d) for _, m in used] for d in days]
    out = {"proxied": sorted(set(proxied)), "start": days[0]}
    for k in horizons:
        rets = [sum(w * b / a for w, a, b in zip(weights, levels[i], levels[i + k], strict=True)) - 1
                for i in range(len(days) - k)]
        if rets:
            out[k] = {"p5": _quantile(rets, 0.05), "p50": _quantile(rets, 0.5),
                      "p_below_20": sum(x <= -0.20 for x in rets) / len(rets), "n": len(rets)}
    return out
