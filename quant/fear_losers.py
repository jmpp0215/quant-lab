"""Fear-time losers, forward record (RESEARCH_LOG section 50) - pure.

Section 49 found the losers reversal in extreme fear is within-sector: a
loser's rebound is the part it fell more than its own sector, while a
whole sector's fall doesn't come back within a month. Section 50 picks
losers by that sector-relative fall and judges them only on records made
from here on (fear_losers_log.py). Nothing here feeds a trading decision.

Snapshots are KRX per-date KOSPI market-cap tables (pykrx get_market_cap):
index = 6-digit code, columns 종가, 거래량, 거래대금, 시가총액, 상장주식수.
"""

import re

import pandas as pd

N, LIQ_CUT, MIN_PEERS = 30, 0.30, 5

# KSIC industry (KRX listing 'Industry') -> ~20 KOSPI-style groups, copied
# from scratch/pbr_sector/sectors.py (section 30) so section 49's grouping
# is the one committed and used here.
GROUPS = [
    ("지주·투자", ["기타 금융업", "회사 본부", "신탁업 및 집합투자"]),
    ("은행·보험·증권", ["은행", "보험", "금융 지원 서비스"]),
    ("부동산", ["부동산"]),
    ("음식료", ["식품", "음료", "담배", "수산물", "도축", "곡물", "유지", "과자", "사료", "어로", "알코올"]),
    ("섬유의복", ["봉제의복", "방적", "직물", "화학섬유", "가죽", "신발", "귀금속"]),
    ("종이목재", ["펄프", "골판지", "종이", "제재", "나무제품", "가구"]),
    ("의약", ["의약"]),
    ("화학", ["화학", "플라스틱", "고무", "비료", "석유 정제"]),
    ("비금속", ["시멘트", "요업", "유리", "비금속 광물"]),
    ("철강금속", ["철강", "비철금속", "금속제품", "금속 가공", "금속 주조"]),
    ("운수장비", ["자동차 신품", "자동차용 엔진", "선박", "항공기", "운송장비 제조"]),
    ("의료정밀", ["정밀기기", "의료용 기기", "악기"]),
    ("전기전자", ["전자부품", "반도체", "전동기", "전지", "절연선", "영상 및 음향", "통신 및 방송 장비",
                 "컴퓨터 및 주변", "가정용 기기", "전구", "전기장비"]),
    ("기계", ["기계 제조", "무기 및 총포"]),
    ("전기가스", ["연료용 가스", "전기업", "증기"]),
    ("건설", ["건설", "건축기술", "공사업"]),
    ("운수창고", ["운송업", "운송관련", "운송장비 임대"]),
    ("통신", ["전기 통신"]),
    ("유통", ["도매", "소매", "상품 중개", "자동차 판매", "부품 및 내장품 판매"]),
]
OTHER = "서비스·기타"
UNKNOWN = "미분류"
_PREF = re.compile(r"(\d?우[A-C]?|우\(전환\))$")


def group_of(industry) -> str:
    if not isinstance(industry, str) or not industry:
        return UNKNOWN
    for name, keys in GROUPS:
        if any(k in industry for k in keys):
            return name
    return OTHER


def sector_map(codes, names, industries) -> dict[str, str]:
    """code -> group; preferred shares take their common stock's industry."""
    industry = dict(zip(codes, industries, strict=True))
    by_name = dict(zip(names, industries, strict=True))
    out = {}
    for code, name in zip(codes, names, strict=True):
        ind = industry.get(code)
        if not isinstance(ind, str):
            base = _PREF.sub("", name)
            ind = by_name.get(base) if base != name else None
        out[code] = group_of(ind)
    return out


def returns(a: pd.DataFrame, b: pd.DataFrame, delist: float | None) -> pd.Series:
    """a -> b return for every name priced in a, split-adjusted as section 36
    (a share count change of 1.5x or more uses market cap); missing or
    unpriced in b = delist (None leaves it NaN)."""
    a = a[a["종가"] > 0]
    j = b.reindex(a.index)
    ok = j["종가"] > 0
    r_sh = (j["상장주식수"] / a["상장주식수"]).where(a["상장주식수"] > 0, 1.0)
    split = (r_sh >= 1.5) | (r_sh <= 2 / 3)
    r = (j["종가"] / a["종가"] - 1).where(~split, j["시가총액"] / a["시가총액"] - 1)
    return r.where(ok, float("nan") if delist is None else delist)


def pick(prev: pd.DataFrame, cur: pd.DataFrame, sectors: dict[str, str]) -> dict[str, list[str]]:
    """Section 50 portfolios at `cur`, `prev` being 20 trading days earlier.

    universe: priced and traded today, priced 20 days ago, not in the bottom
      30% of today's trading value (section 41's candidates).
    raw: the 30 with the lowest 20-day return (section 41's sleeve).
    sector_rel: the 30 lowest by 20-day return minus the mean of the rest of
      their sector in the universe; names with an unknown sector or fewer
      than 5 sector peers are left out.
    """
    el = cur[(cur["종가"] > 0) & (cur["거래량"] > 0)].index.intersection(prev[prev["종가"] > 0].index)
    liq = cur.loc[el, "거래대금"]
    uni = liq[liq > liq.quantile(LIQ_CUT)].index
    past = returns(prev, cur, None).reindex(uni).dropna()
    raw = past.sort_values(kind="stable").index[:N]
    g = pd.Series({s: sectors.get(s, UNKNOWN) for s in past.index}, dtype=object)
    g = g[g != UNKNOWN]
    p = past.loc[g.index]
    total, count = p.groupby(g).transform("sum"), p.groupby(g).transform("count")
    rel = (p - (total - p) / (count - 1))[count - 1 >= MIN_PEERS]
    sector_rel = rel.sort_values(kind="stable").index[:N]
    return {"universe": sorted(past.index), "raw": list(raw), "sector_rel": list(sector_rel)}


def portfolio_return(a: pd.DataFrame, b: pd.DataFrame, names: list[str], delist: float) -> float:
    """Equal-weight a -> b return of `names`."""
    return float(returns(a, b, delist).reindex(names).fillna(delist).mean())
