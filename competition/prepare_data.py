"""Host only: build the competition data from the local KRX snapshots.

Inputs (local, gitignored): KOSPI per-date snapshots every 10 KOSPI trading days.
  scratch/pbr_oos/data/            2005-09-01 .. 2019-11-26 used (cap_*.csv, fund_*.csv)
  scratch/fear_reversal/data_oos/  2019-12-02 .. 2026-10-14 used
  scratch/sector_pbr/raw/1001.csv  KOSPI index, daily

Outputs (gitignored, competition/data/):
  public/train.csv.gz, public/kospi_train.csv   rows dated <= TRAIN_END (handed out)
  ~/.quant-comp-private/full.csv.gz, kospi_full.csv   every row (outside the repo, never handed out)
  dist/quant-comp-public.zip                     bundle for participants outside this repo

Run from the repo root: .venv/bin/python competition/prepare_data.py
"""
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from harness import PRIVATE, TRAIN_END  # one source for the boundary and location

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
SOURCES = [  # (folder, first date used, last date used)
    (REPO / "scratch/pbr_oos/data", "2005-09-01", "2019-11-30"),
    (REPO / "scratch/fear_reversal/data_oos", "2019-12-01", "2026-12-31"),
]
KOSPI = REPO / "scratch/sector_pbr/raw/1001.csv"

COLS = {"티커": "code", "종가": "close", "시가총액": "mktcap", "거래량": "volume",
        "거래대금": "value", "상장주식수": "shares", "BPS": "bps", "PER": "per",
        "PBR": "pbr_krx", "EPS": "eps", "DIV": "div", "DPS": "dps"}


def holder_return(price_ratio, share_ratio, cap_ratio):
    """Copy of scratch/share_rule.py (2026-11-22 rule, research/pbr.md §70 rerun).

    Listed shares up >=1.5x with price x shares > 1.5 is an issuance -> price ratio.
    Any other share change >=1.5x or <=2/3 (split, consolidation, capital reduction)
    -> market-cap ratio. No share change -> price ratio.
    """
    changed = (share_ratio >= 1.5) | (share_ratio <= 2 / 3)
    issuance = (share_ratio >= 1.5) & (price_ratio * share_ratio > 1.5)
    use_cap = changed & ~issuance
    return (price_ratio - 1).where(~use_cap, cap_ratio - 1)


def load_snapshots() -> pd.DataFrame:
    frames = []
    for folder, first, last in SOURCES:
        for cap_path in sorted(folder.glob("cap_*.csv")):
            day = pd.Timestamp(cap_path.stem[4:])
            if not (pd.Timestamp(first) <= day <= pd.Timestamp(last)):
                continue
            cap = pd.read_csv(cap_path, dtype={"티커": str})
            fund = pd.read_csv(folder / f"fund_{cap_path.stem[4:]}.csv",
                               dtype={"티커": str})
            df = cap.merge(fund, on="티커", how="left").rename(columns=COLS)
            df["date"] = day
            frames.append(df[["date", *COLS.values()]])
    panel = pd.concat(frames, ignore_index=True)
    panel["code"] = panel["code"].str.zfill(6)
    panel = panel.sort_values(["code", "date"]).reset_index(drop=True)
    # KRX returns every BPS/EPS as 0 on 2008-08-14 .. 2008-11-25 (8 snapshots).
    # Carry the last real report-based values forward (stale, never future);
    # price-based ratios on those dates are left blank. RULES.md §4.
    empty = panel.groupby("date")["bps"].transform(lambda x: (x > 0).sum() == 0)
    panel["fund_filled"] = empty
    for c in ("bps", "eps", "dps"):
        panel[c] = panel[c].mask(empty).groupby(panel["code"]).ffill()
    panel[["per", "pbr_krx", "div"]] = panel[["per", "pbr_krx", "div"]].mask(empty)
    return panel.sort_values(["date", "code"]).reset_index(drop=True)


def add_returns(panel: pd.DataFrame) -> pd.DataFrame:
    dates = np.sort(panel["date"].unique())
    panel["snap"] = panel["date"].map({d: i for i, d in enumerate(dates)})
    panel = panel.sort_values(["code", "snap"]).reset_index(drop=True)
    g = panel.groupby("code", sort=False)
    prev_close, prev_shares, prev_cap = (g[c].shift() for c in
                                         ("close", "shares", "mktcap"))
    ret = holder_return(panel["close"] / prev_close, panel["shares"] / prev_shares,
                        panel["mktcap"] / prev_cap)
    ok = (panel["close"] > 0) & (prev_close > 0) & (prev_shares > 0) & (prev_cap > 0)
    panel["ret"] = ret.where(ok)
    # 10-day moves past +100% / -50% are mostly KRX snapshot artifacts (share count
    # updated a snapshot after a consolidation, bad prints); some are real (preferred
    # squeezes). Flagged, not repaired: RULES.md §4. Labels touching one are dropped.
    panel["suspect"] = (panel["ret"] > 1) | (panel["ret"] < -0.5)
    panel["gap"] = panel["snap"] - g["snap"].shift()  # 1 = previous snapshot present
    return panel.sort_values(["date", "code"]).reset_index(drop=True)


def main() -> None:
    panel = add_returns(load_snapshots())
    kospi = pd.read_csv(KOSPI).rename(columns={
        "날짜": "date", "종가": "close", "등락률": "chg_pct", "PER": "per",
        "PBR": "pbr", "배당수익률": "div_yield"})

    pub, priv, dist = ROOT / "data/public", PRIVATE, ROOT / "data/dist"
    for d in (pub, priv, dist):
        d.mkdir(parents=True, exist_ok=True)
    train = panel[panel["date"] <= TRAIN_END]
    train.to_csv(pub / "train.csv.gz", index=False)
    kospi[kospi["date"] <= TRAIN_END].to_csv(pub / "kospi_train.csv", index=False)
    panel.to_csv(priv / "full.csv.gz", index=False)
    kospi.to_csv(priv / "kospi_full.csv", index=False)

    bundle = dist / "quant-comp-public.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in ("RULES.md", "harness.py", "score.py", "register.py",
                    "LEADERBOARD.md"):
            z.write(ROOT / rel, f"competition/{rel}")
        for sub in ("participants/_template", "participants/baseline"):
            for f in sorted((ROOT / sub).rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    z.write(f, f"competition/{f.relative_to(ROOT)}")
        for f in sorted(pub.iterdir()):
            z.write(f, f"competition/data/public/{f.name}")

    print(f"snapshots {panel['snap'].nunique()}  rows {len(panel)}  "
          f"{panel['date'].min().date()} .. {panel['date'].max().date()}")
    print(f"train rows {len(train)} (<= {TRAIN_END})  bundle {bundle}")


if __name__ == "__main__":
    main()
