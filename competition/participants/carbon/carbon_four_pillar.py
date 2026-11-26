"""Carbon 4축 등가중 순위 결합 모형 (carbon_four_pillar.py).

모든 지표에 동일한 25스냅샷(약 1년) 창과 1/4 동일 백분위 순위 가중치를 적용(파라미터 튜닝 없음):
  1. H1 (가격 앵커·단기 과열 분리): P_{t-2} / max_{s in [t-25, t]} P_s
  2. H2 (저회전 소외주 프리미엄): -mean(거래대금 / 시가총액)
  3. H3a (저변동성·복권형 왜도 회피): -std(10일 수익률, suspect 제외)
  4. H3b (실질 주식수 희석 회피): -누적 신주 발행 비율 (분할·병합 제외)
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

    return 0.25 * (_pct_rank(s1) + _pct_rank(s2) + _pct_rank(s3) + _pct_rank(s4))
