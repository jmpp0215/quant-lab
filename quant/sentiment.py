"""US market fear & greed, approximated the way CNN builds its index (pure).

CNN's own endpoint refuses automated requests, so this rebuilds the four
components that free daily prices can carry (CNN also uses NYSE breadth,
52-week highs/lows and the put/call ratio, which have no free daily
source here). Each component is scored 0-100 as its percentile within its
own last two years, and the index is their mean - 0 extreme fear, 100
extreme greed.

series: Yahoo ticker -> (sorted dates, closes), as price_history.py caches them.
"""

from bisect import bisect_right

WINDOW = 504          # trading days a component is ranked against (~2 years)
HISTORY = 260         # days of index history returned
LABELS = ((25, "극단적 공포"), (45, "공포"), (55, "중립"), (75, "탐욕"), (101, "극단적 탐욕"))
COMPONENTS = (
    ("momentum", "주가 모멘텀", "S&P 500이 125일 평균보다 얼마나 위에 있나"),
    ("volatility", "변동성 (VIX)", "VIX가 50일 평균보다 낮을수록 탐욕"),
    ("safe_haven", "안전자산 수요", "주식과 장기국채의 20일 수익률 차이"),
    ("junk", "정크본드 수요", "하이일드와 중기국채의 20일 수익률 차이"),
)
TICKERS = ("^GSPC", "^VIX", "TLT", "HYG", "IEF")


def label(score: float) -> str:
    return next(name for limit, name in LABELS if score < limit)


def _aligned(series: dict) -> tuple[list[str], dict[str, list[float]]]:
    """S&P 500 trading days, every ticker forward-filled onto them."""
    days = series["^GSPC"][0]
    out = {}
    for t in TICKERS:
        d, v = series[t]
        out[t] = [v[bisect_right(d, day) - 1] if bisect_right(d, day) else None for day in days]
    return days, out


def _raw(days: list[str], px: dict[str, list[float]]) -> dict[str, list[float | None]]:
    """Each component's raw value per day (higher = greedier)."""
    n = len(days)

    def ma(xs, k, i):
        w = xs[i - k + 1:i + 1]
        return sum(w) / k if i >= k - 1 and None not in w else None

    def ret(xs, k, i):
        return xs[i] / xs[i - k] - 1 if i >= k and xs[i] and xs[i - k] else None

    out = {key: [None] * n for key, _, _ in COMPONENTS}
    for i in range(n):
        m = ma(px["^GSPC"], 125, i)
        out["momentum"][i] = px["^GSPC"][i] / m - 1 if m else None
        v = ma(px["^VIX"], 50, i)
        out["volatility"][i] = -(px["^VIX"][i] / v - 1) if v and px["^VIX"][i] else None
        a, b = ret(px["^GSPC"], 20, i), ret(px["TLT"], 20, i)
        out["safe_haven"][i] = a - b if a is not None and b is not None else None
        a, b = ret(px["HYG"], 20, i), ret(px["IEF"], 20, i)
        out["junk"][i] = a - b if a is not None and b is not None else None
    return out


def _percentile(xs: list[float | None], i: int) -> float | None:
    if xs[i] is None:
        return None
    window = [x for x in xs[max(0, i - WINDOW + 1):i + 1] if x is not None]
    if len(window) < WINDOW // 2:
        return None
    return 100 * sum(x < xs[i] for x in window) / (len(window) - 1) if len(window) > 1 else 50.0


def fear_greed(series: dict) -> dict:
    """{"date", "score", "label", "components": [...], "history": [[date, score]],
    "week_ago", "month_ago"}, or {} without the inputs."""
    if any(t not in series for t in TICKERS):
        return {}
    days, px = _aligned(series)
    raw = _raw(days, px)
    history = []
    for i in range(max(0, len(days) - HISTORY), len(days)):
        scores = [_percentile(raw[k], i) for k, _, _ in COMPONENTS]
        if None not in scores:
            history.append([days[i], sum(scores) / len(scores)])
    if not history:
        return {}
    last = len(days) - 1
    comps = [{"key": k, "label": name, "note": note, "score": _percentile(raw[k], last),
              "value": raw[k][last]} for k, name, note in COMPONENTS]
    score = history[-1][1]

    def back(k):
        return history[-1 - k][1] if len(history) > k else None
    return {"date": history[-1][0], "score": score, "label": label(score), "components": comps,
            "history": history, "week_ago": back(5), "month_ago": back(21)}
