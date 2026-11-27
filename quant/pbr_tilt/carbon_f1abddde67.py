"""Carbon 60일 수익률 중립화 4축 등가중 모형 (carbon_four_pillar_orth.py).

4축 등가중 순위 점수(H1 + H2 + H3a + H3b)에서 결정일 단면마다 60거래일(6스냅샷) 반전 순위와의
선형 상관 성분을 직교 제거(OLS 잔차화)하여, 급락 후 단기 반등장(2009·2020류)에서의 60일 추세 역풍을 차단함.
추가 가중치 파라미터 0개.
"""
import numpy as np
import pandas as pd


def _pct_rank(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if s.isna().all():
        return pd.Series(0.5, index=s.index)
    return s.fillna(s.median()).rank(pct=True, method="average")


def predict(hist, universe, as_of, kospi, ext_dir) -> pd.Series:
    last = hist["snap"].max()
    w = (hist[(hist["snap"] >= last - 25) & hist["code"].isin(universe)]
         .drop_duplicates(["snap", "code"], keep="first")
         .sort_values(["code", "snap"])
         .copy())

    # 1. H1: 52-week high proximity separated from 20-day short-term spike
    first_row = ~w["code"].duplicated()
    r_step = w["ret"].where(~w["suspect"].astype(bool)).fillna(0.0).where(~first_row, 0.0)
    w["level"] = (1.0 + r_step).groupby(w["code"], sort=False).cumprod()
    prev2 = w[w["snap"] <= last - 2].groupby("code")["level"].last()
    high = w.groupby("code")["level"].max()
    s1 = (prev2 / high).reindex(universe)

    # 2. H2: Low dollar share turnover
    wt = w[(w["mktcap"] > 0) & (w["volume"] > 0)]
    turn = (wt["value"] / wt["mktcap"]).groupby(wt["code"]).mean()
    s2 = (-turn).reindex(universe)

    # 3. H3a: Low realized 10-day return volatility
    w_ret = w[w["snap"] > last - 25]
    clean_ret = w_ret["ret"].where(~w_ret["suspect"].astype(bool))
    cnt = clean_ret.groupby(w_ret["code"]).count()
    vol = clean_ret.groupby(w_ret["code"]).std(ddof=1).where(cnt >= 5)
    s3 = (-vol).reindex(universe)

    # 4. H3b: Absence of non-split share dilution
    g = w.groupby("code", sort=False)
    prev_shares = g["shares"].shift(1)
    prev_close = g["close"].shift(1)
    sr = w["shares"] / prev_shares
    pr = w["close"] / prev_close
    split_or_consol = ((sr >= 1.5) & (pr * sr <= 1.5)) | (sr <= 2 / 3)
    valid = (prev_shares > 0) & (prev_close > 0) & (~w["suspect"].astype(bool))
    w["dil"] = sr.where((sr > 1.005) & (~split_or_consol) & valid, 1.0).fillna(1.0)
    cum_dil = w.groupby("code")["dil"].prod()
    s4 = (-cum_dil).reindex(universe)

    raw = 0.25 * (_pct_rank(s1) + _pct_rank(s2) + _pct_rank(s3) + _pct_rank(s4))

    # Cross-sectional orthogonalization against 60-day reversal (6 snapshots)
    w6 = w[w["snap"] > last - 6]
    r6 = w6["ret"].where(~w6["suspect"].astype(bool)).fillna(0.0)
    past6 = (1.0 + r6).groupby(w6["code"]).prod() - 1.0
    rev60 = (-past6).reindex(universe)

    ry = raw.rank()
    rx = _pct_rank(rev60).rank()
    vx = rx.var()
    if pd.isna(vx) or vx < 1e-12:
        return ry
    beta = float(np.cov(ry, rx)[0, 1] / vx)
    return pd.Series(ry - beta * (rx - rx.mean()), index=universe)
