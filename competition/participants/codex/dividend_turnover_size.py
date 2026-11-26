def predict(hist, universe, as_of, kospi, ext_dir):
    last = hist["snap"].max()
    w = hist.loc[(hist["snap"] > last - 12) & hist["code"].isin(universe)]
    now = w.loc[w["snap"].eq(last)]
    dy = now["dps"].where(now["dps"] > 0) / now["close"].where(now["close"] > 0)
    dividend = dy.groupby(now["code"]).median().reindex(universe).fillna(0)
    turnover = w["volume"] / w["shares"].where(w["shares"] > 0)
    quiet = -turnover.groupby(w["code"]).median().reindex(universe)
    ranks = dividend.rank(pct=True) + quiet.rank(pct=True).fillna(0.5)
    cap = now["mktcap"].where(now["mktcap"] > 0).groupby(now["code"]).median()
    small = -cap.reindex(universe)
    return (ranks + small.rank(pct=True).fillna(0.5)) / 3
