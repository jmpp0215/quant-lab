"""claude1 B: average of the issuance rank (A) and the 60-day reversal rank (baseline)."""
import importlib.util
from pathlib import Path

import pandas as pd

_spec = importlib.util.spec_from_file_location("claude1_issuance", Path(__file__).with_name("issuance.py"))
_a = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_a)


def predict(hist, universe, as_of, kospi, ext_dir) -> pd.Series:
    iss = -_a.net_issuance(hist, universe)
    last = hist["snap"].max()
    w = hist[(hist["snap"] > last - 6) & hist["code"].isin(universe)]
    r = w["ret"].where(~w["suspect"].astype(bool)).fillna(0)
    rev = -((1 + r).groupby(w["code"]).prod() - 1).reindex(universe)
    return (iss.rank() + rev.rank()) / 2
