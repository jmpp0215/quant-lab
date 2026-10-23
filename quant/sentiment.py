"""Market fear & greed indices (pure): 0 extreme fear, 100 extreme greed.

Each component is scored 0-100 as its percentile within its own last two
years, and an index is the mean of its components.

US: CNN's index approximated - its endpoint refuses automated requests, so
this rebuilds the four components free daily prices can carry (CNN also
uses NYSE breadth, 52-week highs/lows and the put/call ratio).

Korea: our own, in CNN's spirit, from series Yahoo has since 2004 so it
reaches back through 2008 (RESEARCH_LOG section 38 uses its history). VKOSPI isn't
on Yahoo or pykrx's index lists, so KOSPI's realized volatility stands in.
The bond leg (114260, KOSEF 국고채3년) only starts 2009-07; before that
the index is the mean of the other three.

series: Yahoo ticker -> (sorted dates, closes), as price_history.py caches them.
"""

import math
from bisect import bisect_right

WINDOW = 504          # trading days a component is ranked against (~2 years)
HISTORY = 260         # days of index history shown
LABELS = ((25, "극단적 공포"), (45, "공포"), (55, "중립"), (75, "탐욕"), (101, "극단적 탐욕"))
COMPONENTS = (
    ("momentum", "주가 모멘텀", "S&P 500이 125일 평균보다 얼마나 위에 있나"),
    ("volatility", "변동성 (VIX)", "VIX가 50일 평균보다 낮을수록 탐욕"),
    ("safe_haven", "안전자산 수요", "주식과 장기국채의 20일 수익률 차이"),
    ("junk", "정크본드 수요", "하이일드와 중기국채의 20일 수익률 차이"),
)
TICKERS = ("^GSPC", "^VIX", "TLT", "HYG", "IEF")
KR_COMPONENTS = (
    ("momentum", "주가 모멘텀", "코스피가 125일 평균보다 얼마나 위에 있나"),
    ("volatility", "변동성", "코스피 20일 실현변동성이 50일 평균보다 낮을수록 탐욕"),
    ("fx", "환율", "원/달러가 20일간 덜 오를수록(원화 강세) 탐욕"),
    ("safe_haven", "안전자산 수요", "코스피와 국고채 3년 ETF의 20일 수익률 차이"),
)
KR_TICKERS = ("^KS11", "KRW=X", "114260.KS")
KR_MIN_COMPONENTS = 3


def label(score: float) -> str:
    return next(name for limit, name in LABELS if score < limit)


def _aligned(series: dict, base: str, tickers: tuple[str, ...]):
    """`base`'s trading days, every ticker forward-filled onto them (None
    before a ticker's first date)."""
    days = series[base][0]
    out = {}
    for t in tickers:
        d, v = series[t] if t in series else ([], [])
        out[t] = [v[bisect_right(d, day) - 1] if bisect_right(d, day) else None for day in days]
    return days, out


def _ma(xs, k, i):
    w = xs[i - k + 1:i + 1]
    return sum(w) / k if i >= k - 1 and None not in w else None


def _ret(xs, k, i):
    return xs[i] / xs[i - k] - 1 if i >= k and xs[i] and xs[i - k] else None


def _diff(a, b):
    return a - b if a is not None and b is not None else None


def _raw(days: list[str], px: dict) -> dict[str, list[float | None]]:
    """US components per day (higher = greedier)."""
    out = {key: [None] * len(days) for key, _, _ in COMPONENTS}
    for i in range(len(days)):
        m = _ma(px["^GSPC"], 125, i)
        out["momentum"][i] = px["^GSPC"][i] / m - 1 if m else None
        v = _ma(px["^VIX"], 50, i)
        out["volatility"][i] = -(px["^VIX"][i] / v - 1) if v and px["^VIX"][i] else None
        out["safe_haven"][i] = _diff(_ret(px["^GSPC"], 20, i), _ret(px["TLT"], 20, i))
        out["junk"][i] = _diff(_ret(px["HYG"], 20, i), _ret(px["IEF"], 20, i))
    return out


def _kr_raw(days: list[str], px: dict) -> dict[str, list[float | None]]:
    """Korean components per day (higher = greedier)."""
    k = px["^KS11"]
    n = len(days)
    daily = [math.log(k[i] / k[i - 1]) if i and k[i] and k[i - 1] else None for i in range(n)]
    rv = [None] * n
    for i in range(20, n):
        w = daily[i - 19:i + 1]
        if None not in w:
            m = sum(w) / 20
            rv[i] = math.sqrt(sum((x - m) ** 2 for x in w) / 19)
    out = {key: [None] * n for key, _, _ in KR_COMPONENTS}
    for i in range(n):
        m = _ma(k, 125, i)
        out["momentum"][i] = k[i] / m - 1 if m else None
        v = _ma(rv, 50, i)
        out["volatility"][i] = -(rv[i] / v - 1) if v and rv[i] else None
        f = _ret(px["KRW=X"], 20, i)
        out["fx"][i] = -f if f is not None else None
        out["safe_haven"][i] = _diff(_ret(k, 20, i), _ret(px["114260.KS"], 20, i))
    return out


def _percentile(xs: list[float | None], i: int) -> float | None:
    if xs[i] is None:
        return None
    window = [x for x in xs[max(0, i - WINDOW + 1):i + 1] if x is not None]
    if len(window) < WINDOW // 2:
        return None
    return 100 * sum(x < xs[i] for x in window) / (len(window) - 1) if len(window) > 1 else 50.0


def _history(days, raw, comps, start: int, min_components: int) -> list[list]:
    out = []
    for i in range(start, len(days)):
        scores = [s for s in (_percentile(raw[k], i) for k, _, _ in comps) if s is not None]
        if len(scores) >= min_components:
            out.append([days[i], sum(scores) / len(scores)])
    return out


def _summary(days, raw, comps, min_components: int) -> dict:
    history = _history(days, raw, comps, max(0, len(days) - HISTORY), min_components)
    if not history:
        return {}
    last = len(days) - 1
    score = history[-1][1]

    def back(k):
        return history[-1 - k][1] if len(history) > k else None
    return {"date": history[-1][0], "score": score, "label": label(score),
            "components": [{"key": k, "label": name, "note": note, "score": _percentile(raw[k], last),
                            "value": raw[k][last]} for k, name, note in comps],
            "history": history, "week_ago": back(5), "month_ago": back(21)}


def fear_greed(series: dict) -> dict:
    """US index: {"date", "score", "label", "components", "history",
    "week_ago", "month_ago"}, or {} without the inputs."""
    if any(t not in series for t in TICKERS):
        return {}
    days, px = _aligned(series, "^GSPC", TICKERS)
    return _summary(days, _raw(days, px), COMPONENTS, len(COMPONENTS))


def kr_fear_greed(series: dict) -> dict:
    """Korean index, same shape as fear_greed()."""
    if any(t not in series for t in ("^KS11", "KRW=X")):
        return {}
    days, px = _aligned(series, "^KS11", KR_TICKERS)
    return _summary(days, _kr_raw(days, px), KR_COMPONENTS, KR_MIN_COMPONENTS)


def kr_history(series: dict) -> list[list]:
    """The Korean index on every day it can be computed - for research."""
    if any(t not in series for t in ("^KS11", "KRW=X")):
        return []
    days, px = _aligned(series, "^KS11", KR_TICKERS)
    return _history(days, _kr_raw(days, px), KR_COMPONENTS, 0, KR_MIN_COMPONENTS)
