import math

import numpy as np
import pandas as pd
import pytest

from quant import value_spread as vs


def snap(rows: dict[str, tuple]) -> pd.DataFrame:
    """code -> (close, volume, shares, BPS); market cap = close * shares."""
    df = pd.DataFrame.from_dict(rows, orient="index", columns=["종가", "거래량", "상장주식수", "BPS"])
    df["시가총액"] = df["종가"] * df["상장주식수"]
    return df


def ladder(n: int, bps: float = 100.0) -> pd.DataFrame:
    """n eligible names with PBR 0.1, 0.2, ... (close = 10*k, BPS 100)."""
    return snap({f"{k:06d}": (10.0 * k, 1, 10, bps) for k in range(1, n + 1)})


class TestSpread:
    def test_median_ratio_and_bottom(self):
        s = ladder(200)                        # PBR 0.1 .. 20.0
        sp, bottom, n = vs.spread(s)
        assert n == 200
        assert bottom == [f"{k:06d}" for k in range(1, 51)]
        # market median PBR 10.05, bottom-50 median 2.55
        assert sp == pytest.approx(10.05 / 2.55)

    def test_ineligible_excluded(self):
        s = ladder(60)
        s.loc["000001", "BPS"] = 0             # no book value
        s.loc["000002", "거래량"] = 0          # not traded
        s.loc["000003", "종가"] = 0            # no price
        _, bottom, n = vs.spread(s)
        assert n == 57
        assert not {"000001", "000002", "000003"} & set(bottom)


class TestPercentile:
    def test_needs_min_past(self):
        assert vs.percentile([1.0] * 51, 2.0) is None
        assert vs.percentile([1.0] * 52, 2.0) == 100.0

    def test_window_is_last_125(self):
        past = [10.0] * 100 + list(range(125))  # older values beyond the window are ignored
        assert vs.percentile(past, 62.5) == pytest.approx(100 * 63 / 125)

    def test_strictly_below(self):
        assert vs.percentile([1.0] * 60, 1.0) == 0.0


class TestReturns:
    def test_plain_split_and_delist(self):
        a = snap({"a": (100, 1, 10, 50), "b": (100, 1, 10, 50), "c": (100, 1, 10, 50)})
        b = snap({"a": (110, 1, 10, 50), "b": (55, 1, 20, 50)})   # b split 2:1, c gone
        r = vs.step(a, b, delist=0.0)
        assert r["a"] == pytest.approx(0.10)
        assert r["b"] == pytest.approx(0.10)    # market cap 1000 -> 1100
        assert r["c"] == 0.0
        assert vs.step(a, b, delist=-1.0)["c"] == -1.0

    def test_period_returns_bottom_vs_all(self):
        a = ladder(100)
        b = a.copy()
        b.loc[[f"{k:06d}" for k in range(1, 51)], "종가"] *= 1.10   # cheap half +10%
        top, ew = vs.period_returns(a, b)
        assert top == pytest.approx(0.10)
        assert ew == pytest.approx(0.05)

    def test_excess_compounds(self):
        assert vs.excess([(0.10, 0.0), (0.10, 0.05)]) == pytest.approx(1.21 - 1.05)


class TestStats:
    def test_non_overlapping(self):
        assert vs.non_overlapping([0, 1, 2, 24, 30, 48], 24) == 3
        assert vs.non_overlapping([], 24) == 0

    def test_nw_lag0_is_hc0(self):
        x = np.array([1.0, 2, 3, 4, 5, 6])
        y = np.array([1.0, 3, 2, 5, 4, 7])
        X = np.column_stack([np.ones(6), x])
        XtXi = np.linalg.inv(X.T @ X)
        u = y - X @ (XtXi @ X.T @ y)
        se = math.sqrt((XtXi @ (X.T * u**2) @ X @ XtXi)[1, 1])
        b, t = vs.nw_slope(y, x, lag=0)
        assert b == pytest.approx(18 / 17.5)    # Sxy / Sxx by hand
        assert t == pytest.approx(b / se)

    def test_null_bootstrap_p_range(self):
        rng = np.random.default_rng(0)
        x = np.linspace(0, 100, 40)
        p_strong = vs.null_boot_p(x * 0.01 + rng.normal(0, 0.01, 40), x, 5, 199, rng, lag=3)
        p_none = vs.null_boot_p(rng.normal(0, 0.01, 40), x, 5, 199, rng, lag=3)
        assert p_strong <= 0.01
        assert 1 / 200 <= p_none <= 1.0
