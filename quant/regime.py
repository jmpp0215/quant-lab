"""The "expensive but narrow" market warning (RESEARCH_LOG section 47), pure.

KOSPI's PBR in the top 30% of its last 5 years while 30% or fewer KOSPI
stocks trade above their 200-day average. Four past cases, three followed by a
120-day fall - too few to test (section 47 is INCONCLUSIVE), so this is a
warning with context, never a trading signal. The research measured breadth
on 10-day KRX snapshots (delisted included); live breadth here uses the daily
closes of the tracked KOSPI universe (pead_price_raw), the closest thing
refreshed every day.
"""

from statistics import mean

PBR_PCT_MIN = 70
BREADTH_MAX = 30
PCT_WINDOW, PCT_MIN = 1260, 630
SMA, SMA_MIN = 200, 150

# Section 47's cases and the post-hoc review (not a test): what each looked
# like on the day, and the KOSPI 120 trading days later.
CASES = (
    {"date": "2006-06-07", "kind": "일시적 충격", "dd_1y": -0.135, "r20": -0.128, "r60": -0.034,
     "fear": 15, "breadth_3m_max": 64, "after_120d": 0.12},
    {"date": "2008-01-21", "kind": "약세장 초입", "dd_1y": -0.185, "r20": -0.096, "r60": -0.136,
     "fear": 23, "breadth_3m_max": 66, "after_120d": -0.09},
    {"date": "2008-05-06", "kind": "엇갈림", "dd_1y": -0.100, "r20": 0.054, "r60": 0.100,
     "fear": 51, "breadth_3m_max": 27, "after_120d": -0.46},
    {"date": "2021-12-09", "kind": "엇갈림", "dd_1y": -0.083, "r20": 0.036, "r60": -0.027,
     "fear": 45, "breadth_3m_max": 61, "after_120d": -0.13},
)


def trailing_pct(values: list[float]) -> float | None:
    """Percentile (0-100) of the last value within the trailing 5 years."""
    window = values[-PCT_WINDOW:]
    if len(window) < PCT_MIN:
        return None
    last = window[-1]
    return 100 * sum(v < last for v in window[:-1]) / (len(window) - 1)


def breadth(closes_by_symbol: dict[str, list[float]]) -> float | None:
    """Share (%) of symbols whose last close is above their own 200-day
    average; symbols with fewer than 150 closes are left out."""
    above = [c[-1] > mean(c[-SMA:]) for c in closes_by_symbol.values() if len(c) >= SMA_MIN]
    return 100 * sum(above) / len(above) if above else None


def features(closes: list[float]) -> dict:
    """Drawdown from the 1-year high and 20/60-day returns of the index."""
    last = closes[-1]
    return {"dd_1y": last / max(closes[-250:]) - 1,
            "r20": last / closes[-21] - 1 if len(closes) > 20 else None,
            "r60": last / closes[-61] - 1 if len(closes) > 60 else None}


def triggered(pbr_pct: float | None, breadth_pct: float | None) -> bool:
    return pbr_pct is not None and breadth_pct is not None and \
        pbr_pct >= PBR_PCT_MIN and breadth_pct <= BREADTH_MAX
