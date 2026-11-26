"""Carbon 비추세 구조 3축 등가중 모형 (carbon_structural_three.py).

주가의 방향성(52주 고가·수익률 부호)을 전혀 사용하지 않고, 세 가지 비방향성 구조 지표만
동일 백분위 순위 가중치(각 1/3, 전 지표 25스냅샷 창)로 결합:
  1. H2 (저회전 소외주 프리미엄): -mean(거래대금 / 시가총액)
  2. H3a (저변동성·복권형 왜도 회피): -std(10일 수익률, suspect 제외)
  3. H3b (실질 주식수 희석 회피): -누적 신주 발행 비율 (분할·병합 제외)
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

    # 1. H2: Low dollar share turnover
    wt = w[(w["mktcap"] > 0) & (w["volume"] > 0)]
    turn = (wt["value"] / wt["mktcap"]).groupby(wt["code"]).mean()
    s2 = (-turn).reindex(universe)

    # 2. H3a: Low realized 10-day return volatility
    w_ret = w[w["snap"] > last - 25]
    clean_ret = w_ret["ret"].where(~w_ret["suspect"].astype(bool))
    cnt = clean_ret.groupby(w_ret["code"]).count()
    vol = clean_ret.groupby(w_ret["code"]).std(ddof=1).where(cnt >= 5)
    s3 = (-vol).reindex(universe)

    # 3. H3b: Absence of non-split share dilution
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

    return (_pct_rank(s2) + _pct_rank(s3) + _pct_rank(s4)) / 3.0
