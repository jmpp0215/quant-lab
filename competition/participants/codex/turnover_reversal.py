"""낮은 회전율과 최근 가격 하락의 단면 백분위 순위 평균."""


def predict(hist, universe, as_of, kospi, ext_dir):
    last = hist["snap"].max()
    w = hist.loc[(hist["snap"] > last - 12) & hist["code"].isin(universe)]
    turnover = w["volume"] / w["shares"].where(w["shares"] > 0)
    quiet = -turnover.groupby(w["code"]).median().reindex(universe)

    recent = w.loc[w["snap"] > last - 2]
    valid = recent["gap"].eq(1) & ~recent["suspect"].astype(bool)
    gross = (1 + recent["ret"]).where(valid)
    reversal = -gross.groupby(recent["code"]).prod(min_count=2).reindex(universe)
    return (quiet.rank(pct=True).fillna(0.5) + reversal.rank(pct=True).fillna(0.5)) / 2
