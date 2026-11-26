"""H3b: 유상증자·전환권 행사 등 실질 주식수 희석 회피 단독 신호.

직전 25스냅샷 동안 액면분할·무상증자(주가 반비례 조정으로 시가총액 비율 ≈ 1)가 아닌
실질 신주 발행 구간에서의 누적 주식수 증가율에 음(-)의 부호 부여.
"""
import pandas as pd


def predict(hist, universe, as_of, kospi, ext_dir) -> pd.Series:
    last = hist["snap"].max()
    w = (hist[(hist["snap"] >= last - 25) & hist["code"].isin(universe)]
         .drop_duplicates(["snap", "code"], keep="first")
         .sort_values(["code", "snap"])
         .copy())
    g = w.groupby("code", sort=False)
    prev_shares = g["shares"].shift(1)
    prev_close = g["close"].shift(1)
    sr = w["shares"] / prev_shares
    pr = w["close"] / prev_close
    issuance = (((sr > 1.005) & (sr < 1.5) & (pr * sr > 1.02))
                | ((sr >= 1.5) & (pr * sr > 1.5)))
    valid = (prev_shares > 0) & (prev_close > 0) & (~w["suspect"].astype(bool))
    w["dil"] = sr.where(issuance & valid, 1.0).fillna(1.0)
    cum_dil = w.groupby("code")["dil"].prod()
    return (-cum_dil).reindex(universe)
