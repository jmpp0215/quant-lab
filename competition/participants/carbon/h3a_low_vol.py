"""H3a: 저변동성 단독 신호.

직전 25스냅샷(약 1년) 동안 의심 구간(suspect)을 제외한 10거래일 수익률 표준편차의 음(-)의 값.
"""
import pandas as pd


def predict(hist, universe, as_of, kospi, ext_dir) -> pd.Series:
    last = hist["snap"].max()
    w = (hist[(hist["snap"] > last - 25) & hist["code"].isin(universe)]
         .drop_duplicates(["snap", "code"], keep="first")
         .copy())
    w["clean_ret"] = w["ret"].where(~w["suspect"].astype(bool))
    vol = w.groupby("code")["clean_ret"].agg(lambda x: x.std(ddof=1) if x.count() >= 5 else float("nan"))
    return (-vol).reindex(universe)
