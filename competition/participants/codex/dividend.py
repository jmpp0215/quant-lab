def predict(hist, universe, as_of, kospi, ext_dir):
    last = hist["snap"].max()
    w = hist.loc[(hist["snap"] > last - 12) & hist["code"].isin(universe)]
    now = w.loc[w["snap"].eq(last)]
    dy = now["dps"].where(now["dps"] > 0) / now["close"].where(now["close"] > 0)
    dividend = dy.groupby(now["code"]).median().reindex(universe).fillna(0)
    return dividend
