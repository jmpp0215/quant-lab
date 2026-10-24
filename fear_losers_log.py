"""Forward record of fear-time losers (RESEARCH_LOG section 50,
quant/fear_losers.py) - record only, never trades.

    python fear_losers_log.py            record any due days (daily.py does this after the close)
    python fear_losers_log.py report     status; results only once the pre-registered sample is reached
    python fear_losers_log.py report --force   results regardless (looking early, say so in the log)

A day is recorded when it is a baseline day (every 10th KOSPI trading day
from START) or a fear day (Korean fear & greed index <= 25, at most one per
10 trading days). Each record keeps the three portfolios picked at that
close; returns are computed later in `report` from KRX snapshots 20 trading
days on. Days are processed only once they are before today, so the 15:40
run never reads a KRX close that isn't final; a missed run is caught up.

KRX via pykrx needs the login in .env and prints the login id, so its
output is swallowed (as regime_check.update_fund). Two requests per
recorded day plus a monthly listing refresh.
"""

import contextlib
import io
import json
import logging
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd
from dotenv import load_dotenv

import price_history
from quant import fear_losers, market, sentiment, storage

DIR = storage.DB_PATH.parent / "fear_losers"
SNAP, LOG = DIR / "snap", DIR / "log"
LISTING = DIR / "krx_desc.csv"
START = "2026-10-23"
EVERY, HOLD, FEAR = 10, 20, 25
COST = 2 * 0.00015 + 2 * 0.003 + 0.0015      # section 26 round trip
MIN_FEAR_DAYS, MIN_EPISODES = 10, 4
LISTING_MAX_AGE = 30

log = logging.getLogger("fear_losers_log")


@contextlib.contextmanager
def quiet():
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


def snapshot(day: str) -> pd.DataFrame:
    """KOSPI market-cap table for an ISO day, cached."""
    SNAP.mkdir(parents=True, exist_ok=True)
    f = SNAP / f"cap_{day.replace('-', '')}.csv"
    if not f.exists():
        load_dotenv(storage.DB_PATH.parents[1] / ".env")
        with quiet():
            from pykrx import stock
            df = stock.get_market_cap(day.replace("-", ""), market="KOSPI")
        if df is None or df.empty or not (df["종가"] > 0).any():
            raise ValueError(f"KRX snapshot empty for {day}")
        df.to_csv(f)
    df = pd.read_csv(f, index_col=0, dtype={0: str})
    df.index = df.index.astype(str).str.zfill(6)
    return df[~df.index.duplicated()]


def sectors(today: date) -> tuple[dict[str, str], str]:
    """Sector map from the KRX listing, refreshed monthly; the old copy is
    kept if a refresh fails."""
    DIR.mkdir(parents=True, exist_ok=True)
    age = (today - datetime.fromtimestamp(LISTING.stat().st_mtime, market.KST).date()).days if LISTING.exists() else None
    if age is None or age > LISTING_MAX_AGE:
        try:
            with quiet():
                import FinanceDataReader as fdr
                fdr.StockListing("KRX-DESC").to_csv(LISTING, index=False)
        except Exception:
            if not LISTING.exists():
                raise
            log.warning("listing refresh failed; using the copy from %d days ago", age)
    df = pd.read_csv(LISTING, dtype={"Code": str})
    stamp = datetime.fromtimestamp(LISTING.stat().st_mtime, market.KST).date().isoformat()
    return fear_losers.sector_map(df["Code"], df["Name"], df["Industry"]), stamp


def fear_by_day() -> tuple[list[str], dict[str, float]]:
    series, _ = price_history.load_all()
    days = price_history.load("^KS11")[0]
    return days, {d: v for d, v in sentiment.kr_history(series)}


def record(d: str, days: list[str], fear: float | None, kinds: list[str], today: date) -> dict:
    i = days.index(d)
    prev = days[i - HOLD]
    sec, stamp = sectors(today)
    p = fear_losers.pick(snapshot(prev), snapshot(d), sec)
    entry = {"date": d, "prev": prev, "fear": fear, "kinds": kinds, "listing": stamp,
             "n_universe": len(p["universe"]), **p}
    LOG.mkdir(parents=True, exist_ok=True)
    (LOG / f"{d}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1))
    return entry


def logs() -> list[dict]:
    return [json.loads(f.read_text()) for f in sorted(LOG.glob("*.json"))] if LOG.exists() else []


def due(days: list[str], fear: dict[str, float], today: date) -> list[tuple[str, list[str]]]:
    """(day, kinds) still to record: on or after START, before today, after the last checked day."""
    state = DIR / "state.json"
    checked = json.loads(state.read_text())["checked"] if state.exists() else ""
    base = days.index(START)
    last_fear = max((days.index(e["date"]) for e in logs() if "fear" in e["kinds"]), default=-EVERY)
    out = []
    for i in range(base, len(days)):
        d = days[i]
        if d <= checked or d >= today.isoformat():
            continue
        kinds = []
        if (i - base) % EVERY == 0:
            kinds.append("baseline")
        f = fear.get(d)
        if f is not None and f <= FEAR and i - last_fear >= EVERY:
            kinds.append("fear")
            last_fear = i
        out.append((d, kinds))
    return out


def run(today: date) -> list[dict]:
    days, fear = fear_by_day()
    made = []
    for d, kinds in due(days, fear, today):
        if kinds:
            made.append(record(d, days, fear.get(d), kinds, today))
        (DIR / "state.json").write_text(json.dumps({"checked": d}))
    return made


def episodes(idx: list[int]) -> list[list[int]]:
    """Fear records grouped when they are at most HOLD trading days apart."""
    out: list[list[int]] = []
    for k, i in enumerate(idx):
        if out and i - idx[k - 1] <= HOLD:
            out[-1].append(k)
        else:
            out.append([k])
    return out


def report(force: bool = False) -> None:
    days, _ = fear_by_day()
    rows = []
    for e in logs():
        i = days.index(e["date"])
        if i + HOLD >= len(days):
            continue
        a, b = snapshot(e["date"]), snapshot(days[i + HOLD])
        row = {"date": e["date"], "i": i, "fear": "fear" in e["kinds"], "base": "baseline" in e["kinds"]}
        for delist in (0.0, -1.0):
            r = {k: fear_losers.portfolio_return(a, b, e[k], delist) for k in ("universe", "raw", "sector_rel")}
            row[f"h9_{delist}"] = r["sector_rel"] - r["universe"] - COST
            row[f"h10_{delist}"] = r["sector_rel"] - r["raw"]
            row[f"sr_{delist}"], row[f"uni_{delist}"] = r["sector_rel"], r["universe"]
        rows.append(row)
    df = pd.DataFrame(rows)
    f = df[df.fear] if len(df) else df
    eps = episodes(f.i.tolist()) if len(f) else []
    print(f"records {len(logs())}, with 20-day results {len(df)}; fear days {len(f)}/{MIN_FEAR_DAYS}, "
          f"episodes {len(eps)}/{MIN_EPISODES}")
    if not force and (len(f) < MIN_FEAR_DAYS or len(eps) < MIN_EPISODES):
        print("판단 불가 - pre-registered sample not reached; results hidden (report --force to look early)")
        return
    verdict = {}
    for h in ("h9", "h10"):
        ok = True
        for delist in (0.0, -1.0):
            col = f"{h}_{delist}"
            ep = [f[col].iloc[e].mean() for e in eps]
            m, pos = f[col].mean(), sum(x > 0 for x in ep)
            rng = np.random.default_rng(20261024)
            boot = [np.mean(rng.choice(ep, len(ep))) for _ in range(10000)]
            print(f"{h} delist {delist:+.0%}: mean {m:+.2%}, episodes positive {pos}/{len(eps)}, "
                  f"bootstrap share <= 0 {np.mean(np.array(boot) <= 0):.1%} (reported)")
            ok = ok and m > 0 and pos >= 0.6 * len(eps)
        verdict[h] = ok
    b = df[df.base & ~df.fear]
    if len(b):
        print(f"(reported) baseline non-fear days n={len(b)}: sector_rel - universe - cost {b['h9_0.0'].mean():+.2%}, "
              f"sector_rel - raw {b['h10_0.0'].mean():+.2%}")
    print(f"H9 (sector_rel beats universe after cost): {'PASS' if verdict['h9'] else 'FAIL'} | "
          f"H10 (sector_rel beats raw): {'PASS' if verdict['h10'] else 'FAIL'}"
          + ("  [forced early look]" if force else ""))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if sys.argv[1:2] == ["report"]:
        report(force="--force" in sys.argv)
    else:
        for e in run(datetime.now(market.KST).date()):
            print(e["date"], e["kinds"], f"fear {e['fear']:.1f}" if e["fear"] is not None else "",
                  "sector_rel:", ", ".join(e["sector_rel"][:10]), "...")
    sys.exit(0)
