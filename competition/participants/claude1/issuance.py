"""claude1 A: net share issuance over the last year (REPORT.md §1).

Per step, log(mktcap / previous mktcap) - log(1 + ret). `ret` treats splits/merges by
the market-cap ratio and new shares by the price ratio (RULES.md §4), so a split adds 0
and only new shares (rights issues, CB conversion) are left; buybacks/cancellations go
negative. Summed over the last 25 snapshots (~1 year). Score = -issuance.
"""
import numpy as np
import pandas as pd

WINDOW = 25
NOISE = 0.01  # |issuance| below 1% is rounding noise in mktcap/close, treated as 0


def net_issuance(hist, universe, window=WINDOW) -> pd.Series:
    last = hist["snap"].max()
    w = hist[(hist["snap"] > last - window - 1) & hist["code"].isin(universe)]
    w = w.sort_values(["code", "snap"])
    prev = w.groupby("code")["mktcap"].shift(1)
    step = np.log(w["mktcap"] / prev) - np.log1p(w["ret"])
    ok = (w["snap"] > last - window) & ~w["suspect"].astype(bool) & (prev > 0) & (w["mktcap"] > 0)
    step = step.where(ok).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    tot = step.groupby(w["code"]).sum().reindex(universe).fillna(0.0)
    return tot.where(tot.abs() >= NOISE, 0.0)


def predict(hist, universe, as_of, kospi, ext_dir) -> pd.Series:
    return -net_issuance(hist, universe)
