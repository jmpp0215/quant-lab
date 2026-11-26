"""claude1 C1: Amihud illiquidity over the last 6 snapshots (REPORT.md §1-2).

Mean of |ret| / value per snapshot step (value = traded value on the snapshot day).
Suspect steps and steps with no trading value are dropped. Higher = less liquid = higher score.
"""
import numpy as np
import pandas as pd

WINDOW = 6


def illiquidity(hist, universe, window=WINDOW) -> pd.Series:
    last = hist["snap"].max()
    w = hist[(hist["snap"] > last - window) & hist["code"].isin(universe)]
    ok = ~w["suspect"].astype(bool) & (w["value"] > 0) & w["ret"].notna()
    x = (w["ret"].abs() / w["value"]).where(ok).replace([np.inf, -np.inf], np.nan)
    return x.groupby(w["code"]).mean().reindex(universe)


def predict(hist, universe, as_of, kospi, ext_dir) -> pd.Series:
    return illiquidity(hist, universe)
