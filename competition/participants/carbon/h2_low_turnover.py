"""H2: 저회전율(소외 가치주) 단독 신호.

직전 25스냅샷(약 1년, 거래량 > 0인 날) 일별 회전율(거래대금 / 시가총액)의 평균에 음(-)의 부호 부여.
회전율이 낮을수록 개인 단기 투기·복권성 매매가 적은 소외 자산주로 보아 높은 점수 부여.
"""
import pandas as pd


def predict(hist, universe, as_of, kospi, ext_dir) -> pd.Series:
    last = hist["snap"].max()
    w = (hist[(hist["snap"] >= last - 25) & hist["code"].isin(universe)]
         .drop_duplicates(["snap", "code"], keep="first"))
    w = w[(w["mktcap"] > 0) & (w["volume"] > 0)].copy()
    w["turn"] = w["value"] / w["mktcap"]
    mean_turn = w.groupby("code")["turn"].mean()
    return (-mean_turn).reindex(universe)
