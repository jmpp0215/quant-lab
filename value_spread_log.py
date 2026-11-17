"""Value spread forward record (RESEARCH_LOG section 57-F, quant/value_spread.py)
- record only, never trades.

    python value_spread_log.py           record any due grid days (daily.py does this after the close)
    python value_spread_log.py report    status; the test runs only once the pre-registered sample is reached

Every 10th KOSPI trading day from START is a grid day. For each, the KOSPI
market-cap and fundamental tables are cached, and the spread, its
percentile (against the history: section 46's 2005-2026-10 series as seed,
then the forward days) and the bottom-50 list are logged. The 12-month
excess is computed in `report` once 24 later grid snapshots exist. The test
(section 57's primary: NW lag 25, null-restricted block bootstrap) runs
only when at least 3 non-overlapping 12-month results exist (about
2029-11); before that results stay hidden, with no early-look option.

Days are processed only once they are before today, so the 15:40 run never
reads a KRX close that isn't final; a missed run is caught up. KRX via
pykrx needs the login in .env and prints the login id, so its output is
swallowed. Two requests per grid day.
"""

import contextlib
import io
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

import price_history
from quant import market, storage
from quant import value_spread as vs

ROOT = Path(__file__).parent
DIR = storage.DB_PATH.parent / "value_spread"
SNAP, LOG = DIR / "snap", DIR / "log"
HISTORY = DIR / "history.csv"
SEED = ROOT / "scratch" / "value_spread" / "spread_history_seed.csv"
START = "2026-11-18"        # first grid day after the section 57 commit (2026-11-17)
EVERY = 10
MIN_INDEPENDENT = 3

log = logging.getLogger("value_spread_log")


@contextlib.contextmanager
def quiet():
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


def snapshot(day: str) -> pd.DataFrame:
    """KOSPI market-cap table + BPS for an ISO day, cached."""
    SNAP.mkdir(parents=True, exist_ok=True)
    key = day.replace("-", "")
    fc, ff = SNAP / f"cap_{key}.csv", SNAP / f"fund_{key}.csv"
    if not (fc.exists() and ff.exists()):
        load_dotenv(ROOT / ".env")
        with quiet():
            from pykrx import stock
            cap = stock.get_market_cap(key, market="KOSPI")
            fund = stock.get_market_fundamental(key, market="KOSPI")
        if cap is None or cap.empty or not (cap["종가"] > 0).any():
            raise ValueError(f"KRX market-cap snapshot empty for {day}")
        if fund is None or fund.empty or not (fund["BPS"] > 0).any():
            raise ValueError(f"KRX fundamental snapshot empty for {day}")
        cap.to_csv(fc)
        fund.to_csv(ff)
    cap = pd.read_csv(fc, index_col=0, dtype={0: str})
    fund = pd.read_csv(ff, index_col=0, dtype={0: str})
    for df in (cap, fund):
        df.index = df.index.astype(str).str.zfill(6)
    cap, fund = cap[~cap.index.duplicated()], fund[~fund.index.duplicated()]
    return cap.join(fund[["BPS"]], how="left")


def history() -> pd.Series:
    """date (ISO) -> spread; seeded once from section 46's grid series."""
    if not HISTORY.exists():
        if not SEED.exists():
            raise FileNotFoundError(f"no spread history and no seed at {SEED}")
        DIR.mkdir(parents=True, exist_ok=True)
        pd.read_csv(SEED, index_col=0).to_csv(HISTORY)
    return pd.read_csv(HISTORY, index_col=0)["spread"]


def grid(days: list[str]) -> list[str]:
    base = next((i for i, d in enumerate(days) if d >= START), None)
    return [] if base is None else days[base::EVERY]


def logs() -> list[dict]:
    return [json.loads(f.read_text()) for f in sorted(LOG.glob("*.json"))] if LOG.exists() else []


def record(d: str) -> dict:
    snap = snapshot(d)
    sp, bottom, n = vs.spread(snap)
    h = history()
    pct = vs.percentile(h[h.index < d].tolist(), sp)
    entry = {"date": d, "spread": sp, "percentile": pct, "n_eligible": n, "bottom50": bottom}
    LOG.mkdir(parents=True, exist_ok=True)
    (LOG / f"{d}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1))
    h[d] = sp
    h.sort_index().rename("spread").to_csv(HISTORY, index_label="date")
    return entry


def run(today: date) -> list[dict]:
    days = price_history.load("^KS11")[0]
    state = DIR / "state.json"
    checked = json.loads(state.read_text())["checked"] if state.exists() else ""
    made = []
    for d in grid(days):
        if d <= checked or d >= today.isoformat():
            continue
        made.append(record(d))
        DIR.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"checked": d}))
    return made


def results() -> pd.DataFrame:
    """Per recorded start: x (percentile) and y (12-month excess) once available."""
    g = [e["date"] for e in logs()]
    rows = []
    for k, e in enumerate(logs()):
        if e["percentile"] is None or k + vs.STEPS_PER_PERIOD * vs.PERIODS >= len(g):
            continue
        per = []
        for j in range(vs.PERIODS):
            a = snapshot(g[k + vs.STEPS_PER_PERIOD * j])
            b = snapshot(g[k + vs.STEPS_PER_PERIOD * (j + 1)])
            per.append(vs.period_returns(a, b))
        rows.append({"date": e["date"], "k": k, "x": e["percentile"], "y": vs.excess(per)})
    return pd.DataFrame(rows)


def report() -> None:
    entries = logs()
    df = results()
    indep = vs.non_overlapping(df.k.tolist(), vs.STEPS_PER_PERIOD * vs.PERIODS) if len(df) else 0
    last = entries[-1] if entries else None
    print(f"records {len(entries)}" + (f" (latest {last['date']}: spread {last['spread']:.2f}, percentile "
                                         f"{last['percentile']:.1f})" if last and last["percentile"] is not None else "")
          + f"; 12-month results {len(df)}; non-overlapping {indep}/{MIN_INDEPENDENT}")
    if indep < MIN_INDEPENDENT:
        print("판단 불가 - pre-registered sample not reached; results hidden")
        return
    rng = np.random.default_rng(57)
    b, t = vs.nw_slope(df.y, df.x)
    p_nw = vs.norm_sf(t)
    p1 = vs.null_boot_p(df.y.to_numpy(), df.x.to_numpy(), 25, 10_000, rng)
    p2 = vs.null_boot_p(df.y.to_numpy(), df.x.to_numpy(), 50, 10_000, rng)
    print(f"b {b * 100:+.2f}%p per 100 percentile pts, NW(25) t {t:.2f} p {p_nw:.4f}, "
          f"null block bootstrap p {p1:.4f} (25) / {p2:.4f} (50)")
    print("판정은 RESEARCH_LOG §57 기준으로 사람이 기록 (전향 표본만, 반기·위상 기준은 표본이 작아 보고만)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if sys.argv[1:2] == ["report"]:
        report()
    else:
        for e in run(datetime.now(market.KST).date()):
            pct = e["percentile"]
            print(e["date"], f"spread {e['spread']:.2f}", f"percentile {pct:.1f}" if pct is not None else "", "n", e["n_eligible"])
    sys.exit(0)
