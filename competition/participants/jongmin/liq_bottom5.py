"""jongmin #4: 52주 총 거래대금 하위 5개 제외 (단독 규칙, 이진 점수).

52주 총 거래대금 = 최근 25개 스냅샷(t 포함)의 value 합 (격자는 10일에 하루만 있으니 표본 합).
없는 스냅샷은 0으로 더해짐(신규 상장은 작게 나옴). 하위 5개 0, 나머지 45개 1.
"""
import pandas as pd


def predict(hist, universe, as_of, kospi, ext_dir) -> pd.Series:
    last = hist["snap"].max()
    w = hist[(hist["snap"] > last - 25) & hist["code"].isin(universe)]
    w = w.drop_duplicates(["snap", "code"], keep="last")
    value52 = w.groupby("code")["value"].sum().reindex(universe).fillna(0)
    score = pd.Series(1.0, index=universe)
    score[value52.sort_values(kind="stable").index[:5]] = 0.0
    return score
