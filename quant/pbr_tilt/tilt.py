"""Pure logic for the §80 forward record (research/pbr.md): snapshot panel, T1 weights,
paper holding returns, rebalance cost, rank IC. No network, clock, or disk I/O."""
import numpy as np
import pandas as pd

# research/pbr.md §26 cost legs: commission 0.015%, slippage 0.3%, sell tax 0.15%.
SELL = 0.00015 + 0.003 + 0.0015
BUY = 0.00015 + 0.003
# A daily close-to-close move past ±50% is a re-basing artifact in the raw price
# table (KRX limit is ±30%), as in portfolio_construction.inverse_vol_weights.
ARTIFACT = 0.5

KRX_COLS = {"종가": "close", "시가총액": "mktcap", "거래량": "volume", "거래대금": "value",
            "상장주식수": "shares", "BPS": "bps"}


def holder_return(price_ratio: pd.Series, share_ratio: pd.Series,
                  cap_ratio: pd.Series) -> pd.Series:
    """Same rule as competition/prepare_data.py (research/pbr.md §70 rerun): a share
    count up >=1.5x with price x shares > 1.5 is an issuance -> price ratio; any other
    share change >=1.5x or <=2/3 -> market-cap ratio; otherwise price ratio."""
    changed = (share_ratio >= 1.5) | (share_ratio <= 2 / 3)
    issuance = (share_ratio >= 1.5) & (price_ratio * share_ratio > 1.5)
    use_cap = changed & ~issuance
    return (price_ratio - 1).where(~use_cap, cap_ratio - 1)


def build_panel(snaps: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Competition-format panel from KRX snapshots {ISO date: frame indexed by code}.

    Columns: date, snap, code, close, mktcap, volume, value, shares, bps, ret, gap,
    suspect - the ones the frozen signal reads, built the way prepare_data.py builds
    them (ret against the code's previous present snapshot, suspect = ret > +100% or
    < -50%). A code listed twice on one day keeps its first row.
    """
    frames = []
    for i, day in enumerate(sorted(snaps)):
        df = snaps[day]
        df = df[~df.index.duplicated(keep="first")].rename(columns=KRX_COLS)
        df = df[[c for c in KRX_COLS.values() if c in df.columns]].copy()
        df["code"] = df.index.astype(str).str.zfill(6)
        df["date"], df["snap"] = pd.Timestamp(day), i
        frames.append(df.reset_index(drop=True))
    p = pd.concat(frames, ignore_index=True).sort_values(["code", "snap"])
    g = p.groupby("code", sort=False)
    pc, ps, pm = (g[c].shift() for c in ("close", "shares", "mktcap"))
    ret = holder_return(p["close"] / pc, p["shares"] / ps, p["mktcap"] / pm)
    ok = (p["close"] > 0) & (pc > 0) & (ps > 0) & (pm > 0)
    p["ret"] = ret.where(ok)
    p["gap"] = p["snap"] - g["snap"].shift()
    p["suspect"] = (p["ret"] > 1) | (p["ret"] < -0.5)
    return p.sort_values(["snap", "code"]).reset_index(drop=True)


def percentile(raw, names: list[str]) -> pd.Series:
    """Score -> percentile rank in (0, 1] among `names`; a missing score is neutral."""
    s = pd.to_numeric(pd.Series(raw), errors="coerce").replace([np.inf, -np.inf], np.nan)
    s = s.reindex(names)
    if s.isna().all():
        return pd.Series(0.5, index=names)
    return s.fillna(s.median()).rank(pct=True, method="average")


def tilt(a0: dict[str, float], q: pd.Series) -> dict[str, float]:
    """§79 T1: weight proportional to A0 weight x (0.5 + q), summing to 1."""
    raw = {s: w * (0.5 + float(q[s])) for s, w in a0.items()}
    total = sum(raw.values())
    return {s: v / total for s, v in raw.items()}


def normalize(w: dict[str, float]) -> dict[str, float]:
    total = sum(w.values())
    return {s: v / total for s, v in w.items()}


def hold(weights: dict[str, float], closes: pd.DataFrame) -> dict:
    """Buy-and-hold `weights` from the first row of `closes` (dates x symbols, raw
    closes) to the last. A halted day or a symbol that stops trading keeps its last
    close (last-price recovery); a symbol with no close at all stays flat. Daily moves
    past ±ARTIFACT count as 0%."""
    syms = list(weights)
    c = closes.reindex(columns=syms).ffill()
    r = (c / c.shift()).iloc[1:]
    artifacts = int(((r - 1).abs() > ARTIFACT).sum().sum())
    r = r.mask((r - 1).abs() > ARTIFACT, 1.0).fillna(1.0)
    growth = r.prod()
    missing = [s for s in syms if c[s].isna().all()]
    w = pd.Series(weights, dtype=float)
    value = w * growth
    return {"gross": float(value.sum() / w.sum() - 1),
            "stock_ret": (growth - 1).to_dict(),
            "end_weights": (value / value.sum()).to_dict(),
            "artifacts": artifacts, "missing": missing}


def trade_cost(prev: dict[str, float] | None, new: dict[str, float]) -> tuple[float, float]:
    """(cost, turnover) of moving from drifted weights `prev` (None = all cash) to `new`."""
    prev = prev or {}
    names = set(prev) | set(new)
    sells = sum(max(prev.get(s, 0.0) - new.get(s, 0.0), 0.0) for s in names)
    buys = sum(max(new.get(s, 0.0) - prev.get(s, 0.0), 0.0) for s in names)
    return sells * SELL + buys * BUY, sells + buys


def rank_ic(score: pd.Series, ret: pd.Series) -> float:
    """Spearman rank correlation over common names; 0 when either side is constant."""
    common = score.index.intersection(ret.index)
    a, b = score[common].astype(float), ret[common].astype(float)
    if len(common) < 3 or a.std() < 1e-12 or b.std() < 1e-12:
        return 0.0
    return float(np.corrcoef(a.rank(), b.rank())[0, 1])
