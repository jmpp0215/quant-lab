"""Competition harness: data loading, the task, and the scoring math.

Shared by everyone. Participants score `train` and `public` themselves through
score.py; `private` is scored once by the host after the deadline (RULES.md §5-§7).
"""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
# Hidden ranges live outside the repo so an agent browsing the working folder never
# stumbles on them (Claude Code asks before reading outside it). RULES.md §9.
PRIVATE = Path.home() / ".quant-comp-private"

TRAIN_END = "2013-12-31"   # train: labels end on or before this date (handed out)
PUBLIC_END = "2016-12-31"  # public leaderboard: decisions after TRAIN_END, labels <= this
DEADLINE = "2026-11-30"    # last day to change FINAL.txt; private scoring from the next day
# All participants agreed to close early (RULES.md change log): scoring opens at this KST time.
CLOSED_AT = "2026-11-26T23:50:00+09:00"
TOP_N = 50                 # universe size: lowest PBR
HOLD = 2                   # label = next 2 snapshots (about 20 trading days)
WARMUP = 25                # first decision needs 25 snapshots (about 250 days) of history
PERIODS_PER_YEAR = 12.5    # 250 trading days / 20
MIN_LABELS = 10            # a date with fewer usable labels is skipped


def load(split: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Snapshot panel and daily KOSPI index for a split."""
    if split == "train":
        panel_path, kospi_path = (DATA / "public/train.csv.gz",
                                  DATA / "public/kospi_train.csv")
    else:
        panel_path, kospi_path = (PRIVATE / "full.csv.gz", PRIVATE / "kospi_full.csv")
        if not panel_path.exists():
            raise SystemExit(f"{split} 채점용 숨긴 데이터가 이 컴퓨터에 없습니다.")
    panel = pd.read_csv(panel_path, dtype={"code": str}, parse_dates=["date"])
    kospi = pd.read_csv(kospi_path, parse_dates=["date"])
    return panel.sort_values(["snap", "code"]).reset_index(drop=True), kospi


def decision_snaps(panel: pd.DataFrame, split: str) -> list[int]:
    """Non-overlapping decision snapshots whose whole label window sits in `split`."""
    dates = panel.groupby("snap")["date"].first()
    last = dates.index.max()
    train_end, public_end = pd.Timestamp(TRAIN_END), pd.Timestamp(PUBLIC_END)
    out = []
    for s in range(WARMUP, last - HOLD + 1, HOLD):
        start, end = dates[s], dates[s + HOLD]
        if split == "train":
            ok = end <= train_end
        elif split == "public":
            ok = start > train_end and end <= public_end
        else:
            ok = start > public_end
        if ok:
            out.append(s)
    return out


def universe(rows: pd.DataFrame) -> list[str]:
    """PBR Top 50 at one snapshot: BPS > 0, close > 0, volume > 0, lowest close/BPS."""
    c = rows[(rows["bps"] > 0) & (rows["close"] > 0) & (rows["volume"] > 0)]
    c = c.assign(pbr=c["close"] / c["bps"]).sort_values(["pbr", "code"])
    return c["code"].head(TOP_N).tolist()


def forward_return(panel: pd.DataFrame, s: int, codes: list[str]) -> pd.Series:
    """Holder return from snapshot s to s+HOLD. A stock that disappears keeps its
    last price (0% after it), the same last-price recovery as research/pbr.md §63.
    NaN when the window holds a `suspect` step (RULES.md §4): not scored that date."""
    w = panel[(panel["snap"] > s) & (panel["snap"] <= s + HOLD)
              & panel["code"].isin(codes)]
    growth = (1 + w["ret"].fillna(0)).groupby(w["code"]).prod()
    fwd = growth.reindex(codes).fillna(1.0) - 1
    bad = w.loc[w["suspect"], "code"].unique()
    return fwd.where(~fwd.index.isin(bad))


def load_submission(path: Path):
    spec = importlib.util.spec_from_file_location(f"sub_{path.parent.name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "predict"):
        raise SystemExit(f"{path}: predict() 함수가 없습니다.")
    return mod


def _clean(scores, codes: list[str], path: Path) -> pd.Series:
    if not isinstance(scores, pd.Series):
        raise SystemExit(f"{path}: predict()는 pandas.Series(종목코드 -> 점수)를 돌려줘야 합니다.")
    s = pd.to_numeric(scores, errors="coerce").replace([np.inf, -np.inf], np.nan)
    s = s.reindex(codes)
    if s.isna().all():
        return pd.Series(0.0, index=codes)  # no opinion = no information
    return s.fillna(s.median())  # a missing stock is treated as neutral


def _rank_ic(a: pd.Series, b: pd.Series) -> float:
    if a.std() < 1e-9 or b.std() < 1e-9:  # constant, or a float-noise residual
        return 0.0
    ra, rb = a.rank(), b.rank()
    return float(np.corrcoef(ra, rb)[0, 1])


def _residual(y: pd.Series, x: pd.Series) -> pd.Series:
    """Rank of y with the part explained by rank of x removed (one-variable OLS)."""
    ry, rx = y.rank(), x.rank()
    if rx.std() == 0:
        return ry - ry.mean()
    beta = np.cov(ry, rx)[0, 1] / rx.var()
    return ry - ry.mean() - beta * (rx - rx.mean())


def run(path: Path, split: str, lag: int = 0, data=None) -> pd.DataFrame:
    """Score one submission file. One row per decision date.

    lag=1 hands the submission history only up to the snapshot before the decision
    (about 10 trading days stale): the delay check of RULES.md §6.
    """
    panel, kospi = data if data is not None else load(split)
    sub = load_submission(path)
    base = load_submission(ROOT / "participants/baseline/submission.py")
    ext_dir = path.parent / "external"
    snap_end = panel["snap"].searchsorted(np.arange(panel["snap"].max() + 2),
                                          side="left")
    rows = []
    for s in decision_snaps(panel, split):
        at = panel.iloc[snap_end[s]:snap_end[s + 1]]
        codes = universe(at)
        view = s - lag
        hist = panel.iloc[:snap_end[view + 1]].copy()
        as_of = hist["date"].max()
        k = kospi[kospi["date"] <= as_of].copy()
        fallback = False
        try:
            raw = sub.predict(hist, codes, as_of, k, ext_dir)
        except Exception:
            # Delay check only: the universe comes from snapshot s but history stops
            # at s-1, so a stock listed or resumed at s is absent from hist. Retry once
            # with the stocks hist has; the rest score neutral. RULES.md change log.
            if lag == 0:
                raise
            present = set(hist.loc[hist["snap"] == view, "code"])
            raw = sub.predict(hist, [c for c in codes if c in present], as_of, k, ext_dir)
            fallback = True
        score = _clean(raw, codes, path)
        b = _clean(base.predict(hist.copy(), codes, as_of, k, ext_dir), codes, path)
        fwd = forward_return(panel, s, codes).dropna()
        if len(fwd) < MIN_LABELS:
            continue
        score, b = score[fwd.index], b[fwd.index]
        top = (score.sort_index().sort_values(ascending=False, kind="stable")
               .index[:len(fwd) // 2])
        rows.append({
            "date": at["date"].iloc[0].date(), "n": len(fwd),
            "ic": _rank_ic(score, fwd),
            "inc_ic": _rank_ic(_residual(score, b), fwd),
            "spread": fwd[top].mean() - fwd.mean(), "fallback": fallback,
        })
    return pd.DataFrame(rows)


def summary(per_date: pd.DataFrame) -> dict:
    n = len(per_date)
    if n == 0:
        return {k: float("nan") for k in ("ic", "ic_t", "hit", "inc_ic", "inc_ic_t",
                                          "spread_ann_pp")} | {"n_dates": 0}

    def t(x):
        sd = x.std(ddof=1)
        return float(x.mean() / (sd / np.sqrt(n))) if n > 1 and sd > 0 else 0.0

    return {
        "n_dates": n,
        "ic": float(per_date["ic"].mean()), "ic_t": t(per_date["ic"]),
        "hit": float((per_date["ic"] > 0).mean()),
        "inc_ic": float(per_date["inc_ic"].mean()), "inc_ic_t": t(per_date["inc_ic"]),
        "spread_ann_pp": float(per_date["spread"].mean() * PERIODS_PER_YEAR * 100),
        "fallback_days": int(per_date["fallback"].sum()),
    }
