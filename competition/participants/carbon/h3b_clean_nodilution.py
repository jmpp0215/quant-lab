"""H3b (정정): prepare_data.py의 분할·병합 판정 규칙과 일치시킨 순수 주식수 희석 회피 신호.

sr < 1.5 구간에서 주가 변화율(pr) 조건을 제거하여 하락장 중 발생한 유상증자·전환권 행사도
누락 없이 포착하고 단기 주가 수익률 혼입을 배제함.
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
    split_or_consol = ((sr >= 1.5) & (pr * sr <= 1.5)) | (sr <= 2 / 3)
    valid = (prev_shares > 0) & (prev_close > 0) & (~w["suspect"].astype(bool))
    issuance = (sr > 1.005) & (~split_or_consol) & valid
    w["dil"] = sr.where(issuance, 1.0).fillna(1.0)
    cum_dil = w.groupby("code")["dil"].prod()
    return (-cum_dil).reindex(universe)
