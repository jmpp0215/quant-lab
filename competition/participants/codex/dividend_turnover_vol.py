def predict(hist, universe, as_of, kospi, ext_dir):
    last = hist["snap"].max()
    w = hist.loc[(hist["snap"] > last - 12) & hist["code"].isin(universe)]
    now = w.loc[w["snap"].eq(last)]
    dy = now["dps"].where(now["dps"] > 0) / now["close"].where(now["close"] > 0)
    dividend = dy.groupby(now["code"]).median().reindex(universe).fillna(0)
    turnover = w["volume"] / w["shares"].where(w["shares"] > 0)
    quiet = -turnover.groupby(w["code"]).median().reindex(universe)
    ranks = dividend.rank(pct=True) + quiet.rank(pct=True).fillna(0.5)
    valid = w["gap"].eq(1) & ~w["suspect"].astype(bool)
    returns = w["ret"].where(valid).groupby(w["code"])
    stable = -returns.std().where(returns.count() >= 6).reindex(universe)
    return (ranks + stable.rank(pct=True).fillna(0.5)) / 3
