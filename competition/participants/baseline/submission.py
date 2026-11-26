"""Host baseline: 60-day reversal. Stocks that fell most over the last 6 snapshots
(about 60 trading days) score highest — the short-term reversal research/pbr.md §55
found inside the low-PBR basket. Every submission's incremental IC is measured
against this score (RULES.md §6)."""
import pandas as pd


def predict(hist, universe, as_of, kospi, ext_dir) -> pd.Series:
    last = hist["snap"].max()
    w = hist[(hist["snap"] > last - 6) & hist["code"].isin(universe)]
    r = w["ret"].where(~w["suspect"].astype(bool)).fillna(0)  # flagged steps count as 0
    past = (1 + r).groupby(w["code"]).prod() - 1
    return -past.reindex(universe)
