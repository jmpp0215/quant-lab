"""직전 12개 스냅샷의 거래량 회전율 중앙값이 낮은 순서."""


def predict(hist, universe, as_of, kospi, ext_dir):
    last = hist["snap"].max()
    w = hist.loc[(hist["snap"] > last - 12) & hist["code"].isin(universe)]
    turnover = w["volume"] / w["shares"].where(w["shares"] > 0)
    return -turnover.groupby(w["code"]).median().reindex(universe)
