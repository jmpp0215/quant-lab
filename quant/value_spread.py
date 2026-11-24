"""Value spread forward record (research/value_spread.md section 57-F) - pure.

Section 57 re-checked section 46 (a wide value spread is followed by a
larger PBR Top 50 excess over the next 12 months) on the data it was found
on, so it could only say how fragile the result is. The forward record is
the only new evidence: every 10th KOSPI trading day the spread, its
percentile and the bottom-50 list are recorded, and the 12-month excess is
filled in from later snapshots (value_spread_log.py). Nothing here feeds a
trading decision.

Snapshots are KRX per-date KOSPI tables: pykrx get_market_cap (종가, 거래량,
시가총액, 상장주식수) joined with get_market_fundamental's BPS. Definitions
follow section 46 exactly (scratch/market_map/stage2.py s46).
"""

import math

import numpy as np
import pandas as pd

N = 50
WINDOW, MIN_PAST = 125, 52          # percentile: past 125 grid values, at least 52
STEPS_PER_PERIOD, PERIODS = 2, 12   # 20-day returns, 12 of them = one year
LAG = 25                            # Newey-West lag (about 25 overlapping starts)


def eligible(snap: pd.DataFrame) -> pd.DataFrame:
    return snap[(snap.BPS > 0) & (snap["종가"] > 0) & (snap["거래량"] > 0)]


def spread(snap: pd.DataFrame) -> tuple[float, list[str], int]:
    """(market median PBR / bottom-50 median PBR, bottom-50 codes, eligible count)."""
    el = eligible(snap)
    pbr = (el["종가"] / el.BPS).sort_values(kind="mergesort")
    return float(pbr.median() / pbr.iloc[:N].median()), list(pbr.index[:N]), len(el)


def percentile(past: list[float], value: float) -> float | None:
    """Share (x100) of the last WINDOW earlier values below `value`; None before MIN_PAST."""
    past = past[-WINDOW:]
    if len(past) < MIN_PAST:
        return None
    return 100 * float(np.mean(np.asarray(past) < value))


def step(a: pd.DataFrame, b: pd.DataFrame, delist: float = 0.0) -> pd.Series:
    """a -> b return for every name priced in a. Shares x1.5+ or x2/3- counts as a
    split and uses the market-cap ratio; missing or unpriced in b = delist."""
    a = a[a["종가"] > 0]
    j = b.reindex(a.index)
    ok = j["종가"] > 0
    r_sh = (j["상장주식수"] / a["상장주식수"]).where(a["상장주식수"] > 0, 1.0)
    split = (r_sh >= 1.5) | (r_sh <= 2 / 3)
    r = (j["종가"] / a["종가"] - 1).where(~split, j["시가총액"] / a["시가총액"] - 1)
    return r.where(ok, delist)


def period_returns(a: pd.DataFrame, b: pd.DataFrame, delist: float = 0.0) -> tuple[float, float]:
    """(bottom-50 mean, eligible equal-weight mean) of a -> b returns, winsorized 1/99."""
    el = eligible(a)
    bottom = spread(a)[1]
    ret = step(a, b, delist)
    ret = ret.clip(ret.quantile(0.01), ret.quantile(0.99))
    return float(ret.reindex(bottom).mean()), float(ret.reindex(el.index).mean())


def excess(periods: list[tuple[float, float]]) -> float:
    """Top 50 compounded minus equal-weight compounded over the given periods."""
    return float(np.prod([1 + t for t, _ in periods]) - np.prod([1 + e for _, e in periods]))


def non_overlapping(starts: list[int], gap: int) -> int:
    """How many start indices are at least `gap` apart, counted greedily in order."""
    n, last = 0, None
    for i in sorted(starts):
        if last is None or i - last >= gap:
            n, last = n + 1, i
    return n


def nw_slope(y, x, lag: int = LAG) -> tuple[float, float]:
    """OLS y = a + b x; (b, Newey-West Bartlett t of b)."""
    y, x = np.asarray(y, float), np.asarray(x, float)
    X = np.column_stack([np.ones(len(y)), x])
    XtXi = np.linalg.inv(X.T @ X)
    beta = XtXi @ X.T @ y
    g = X * (y - X @ beta)[:, None]
    S = g.T @ g
    for k in range(1, min(lag, len(y) - 1) + 1):
        G = g[k:].T @ g[:-k]
        S = S + (1 - k / (lag + 1)) * (G + G.T)
    V = XtXi @ S @ XtXi
    return float(beta[1]), float(beta[1] / math.sqrt(V[1, 1]))


def null_boot_p(y, x, block: int, n_boot: int, rng, lag: int = LAG) -> float:
    """H0 b = 0: y* = mean + circular-block-resampled (y - mean), x fixed.
    One-sided p = (1 + #{t* >= t_obs}) / (n_boot + 1)."""
    y = np.asarray(y, float)
    _, t_obs = nw_slope(y, x, lag)
    e0, n = y - y.mean(), len(y)
    nb = -(-n // block)
    hits = 0
    for _ in range(n_boot):
        starts = rng.integers(0, n, nb)
        idx = ((starts[:, None] + np.arange(block)[None, :]) % n).ravel()[:n]
        hits += nw_slope(y.mean() + e0[idx], x, lag)[1] >= t_obs
    return (1 + hits) / (n_boot + 1)


def norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2))
