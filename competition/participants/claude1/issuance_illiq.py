"""claude1 C2: average of the issuance rank (A) and the illiquidity rank (C1)."""
import importlib.util
from pathlib import Path

import pandas as pd


def _load(name):
    spec = importlib.util.spec_from_file_location(f"claude1_{name}", Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_a, _c1 = _load("issuance"), _load("illiq")


def predict(hist, universe, as_of, kospi, ext_dir) -> pd.Series:
    iss = -_a.net_issuance(hist, universe)
    ill = _c1.illiquidity(hist, universe)
    ill = ill.fillna(ill.median())
    return (iss.rank() + ill.rank()) / 2
