"""H1: 52주 고가 근접도에서 최근 20거래일(2스냅샷) 단기 급등분을 분리한 신호.

P_{t-2} / max_{s in [t-25, t]} P_s
= (P_t / max P_s) * (P_t / P_{t-2})^{-1}
즉 52주 고가 대비 낙폭이 작으면서 최근 20거래일 단기 과열이 아닌(또는 소폭 눌린) 종목에 높은 점수 부여.
"""
import pandas as pd


def predict(hist, universe, as_of, kospi, ext_dir) -> pd.Series:
    last = hist["snap"].max()
    w = (hist[(hist["snap"] >= last - 25) & hist["code"].isin(universe)]
         .drop_duplicates(["snap", "code"], keep="first")
         .sort_values(["code", "snap"])
         .copy())
    first_row = ~w["code"].duplicated()
    r = w["ret"].where(~w["suspect"].astype(bool)).fillna(0.0).where(~first_row, 0.0)
    w["level"] = (1.0 + r).groupby(w["code"]).cumprod()
    prev2 = w[w["snap"] <= last - 2].groupby("code")["level"].last()
    high = w.groupby("code")["level"].max()
    return (prev2 / high).reindex(universe)
