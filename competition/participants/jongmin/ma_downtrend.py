"""jongmin #1: 하락 추세 제외 (20일선 < 60일선이면 0, 아니면 1).

격자가 10거래일 간격이라 이동평균은 스냅샷으로 근사한다.
  수정 가격 수준 L = ret을 이어 곱한 값 (suspect 칸은 0%, 기준선과 같음)
  20일선 = 최근 2개 스냅샷 L의 평균, 60일선 = 최근 6개 스냅샷 L의 평균
최근 6개 스냅샷이 다 없는 종목은 하락 추세 근거가 없으므로 1(제외 안 함).
"""
import pandas as pd


def predict(hist, universe, as_of, kospi, ext_dir) -> pd.Series:
    last = hist["snap"].max()
    w = hist[(hist["snap"] > last - 6) & hist["code"].isin(universe)].sort_values("snap")
    r = w["ret"].where(~w["suspect"].astype(bool)).fillna(0)
    w = w.assign(level=(1 + r).groupby(w["code"]).cumprod())
    g = w.groupby("code")["level"]
    n = g.size()
    ma20 = g.apply(lambda s: s.iloc[-2:].mean())
    ma60 = g.mean()
    down = (ma20 < ma60) & (n == 6)
    return (~down).astype(float).reindex(universe).fillna(1.0)
