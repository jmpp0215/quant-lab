"""T1 tilt forward record (research/pbr.md §80, quant/pbr_tilt) - record only, never trades.

    python pbr_tilt_log.py           record due rebalances and results (daily.py does this after the close)
    python pbr_tilt_log.py report    stage 1 / stage 2 status (visible at any time, §80)

Each full PBR rebalance from START (read from data/pbr_live.db, read-only) is one
event: the live target weights (A0) and the frozen carbon score's T1 weights, scored
on the 10-KOSPI-day KRX snapshot grid that the §78 competition panel used (from
2019-12-02). When the next event exists and its close is in pead_price_raw, the
paper holding result of both baskets over the period is written beside it. Log files
are only ever added, never rewritten. KRX via pykrx needs the login in .env and
prints the login id, so its output is swallowed. Two requests per new grid day.
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import logging
import shutil
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

import price_history
from quant import market, storage
from quant.pbr_tilt import tilt

ROOT = Path(__file__).parent
DIR = storage.DB_PATH.parent / "pbr_tilt"
SNAP, LOG = DIR / "snap", DIR / "log"
SEED = ROOT / "scratch" / "fear_reversal" / "data_oos"   # the competition panel's source
PBR_DB = storage.DB_PATH.parent / "pbr_live.db"
SIGNAL = ROOT / "quant" / "pbr_tilt" / "carbon_f1abddde67.py"
SIGNAL_SHA = "f1abddde67"
GRID_START, EVERY = "2019-12-02", 10
WINDOW = 30            # snapshots handed to the signal (it reads the last 26)
START = "2026-12-18"   # first rebalance recorded (§80)
STAGE1, STAGE2 = 6, 24

log = logging.getLogger("pbr_tilt_log")


@contextlib.contextmanager
def quiet():
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


def trading_days() -> list[str]:
    return price_history.load("^KS11")[0]


def grid(days: list[str]) -> list[str]:
    return days[days.index(GRID_START)::EVERY]


def snapshot(day: str) -> pd.DataFrame:
    """KOSPI market-cap table + fundamentals for an ISO grid day, cached in SNAP."""
    SNAP.mkdir(parents=True, exist_ok=True)
    key = day.replace("-", "")
    fc, ff = SNAP / f"cap_{key}.csv", SNAP / f"fund_{key}.csv"
    if not (fc.exists() and ff.exists()):
        if (SEED / fc.name).exists() and (SEED / ff.name).exists():
            shutil.copy(SEED / fc.name, fc)
            shutil.copy(SEED / ff.name, ff)
        else:
            load_dotenv(ROOT / ".env")
            with quiet():
                from pykrx import stock
                cap = stock.get_market_cap(key, market="KOSPI")
                time.sleep(3)
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


def panel_upto(day: str, days: list[str]) -> pd.DataFrame:
    """The last WINDOW grid snapshots on or before `day`."""
    g = [d for d in grid(days) if d <= day][-WINDOW:]
    return tilt.build_panel({d: snapshot(d) for d in g})


def load_signal():
    sha = hashlib.sha256(SIGNAL.read_bytes()).hexdigest()[:10]
    if sha != SIGNAL_SHA:
        raise RuntimeError(f"{SIGNAL.name} sha {sha} != {SIGNAL_SHA}: frozen signal changed, not recording")
    spec = importlib.util.spec_from_file_location("carbon_signal", SIGNAL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def events() -> dict[str, dict[str, float]]:
    """Full PBR rebalance date (ISO) -> live target weights, from START on."""
    if not PBR_DB.exists():
        return {}
    con = sqlite3.connect(f"file:{PBR_DB}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT trade_date, target_portfolio_json, summary_json FROM pbr_rebalance").fetchall()
    finally:
        con.close()
    out = {}
    for trade_date, target_json, summary_json in rows:
        retry_of = (json.loads(summary_json) if summary_json else {}).get("retry_of")
        if retry_of and retry_of != trade_date:
            continue  # a later-day retry replays its full rebalance's target
        target = json.loads(target_json) if target_json else {}
        if not target or not all(isinstance(v, (int, float)) for v in target.values()):
            continue
        d = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}" if "-" not in trade_date else trade_date
        if d >= START:
            out[d] = {str(k).zfill(6): float(v) for k, v in target.items()}
    return dict(sorted(out.items()))


def record_event(d: str, target: dict[str, float], days: list[str]) -> dict:
    signal_day = days[days.index(d) - 1]
    hist = panel_upto(signal_day, days)
    names = list(target)
    as_of = hist["date"].max()
    raw = load_signal().predict(hist, names, as_of, pd.DataFrame({"date": []}), DIR / "external")
    q = tilt.percentile(raw, names)
    a0 = tilt.normalize(target)
    entry = {"date": d, "signal_day": signal_day, "snapshot": as_of.date().isoformat(),
             "signal_sha": SIGNAL_SHA, "a0": a0, "q": q.to_dict(), "t1": tilt.tilt(a0, q)}
    LOG.mkdir(parents=True, exist_ok=True)
    (LOG / f"{d}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1))
    return entry


def closes(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{storage.DB_PATH}?mode=ro", uri=True)
    try:
        q = (f"SELECT date, symbol, close FROM pead_price_raw WHERE date BETWEEN ? AND ? "
             f"AND symbol IN ({','.join('?' * len(symbols))})")
        df = pd.read_sql_query(q, con, params=[start.replace("-", ""), end.replace("-", ""), *symbols])
    finally:
        con.close()
    return df.pivot(index="date", columns="symbol", values="close").sort_index()


def record_result(e: dict, nxt: str, prev: dict | None) -> dict:
    names = sorted(set(e["a0"]) | set(e["t1"]))
    c = closes(names, e["date"], nxt)
    if c.empty or c.index[-1] != nxt.replace("-", "") or c.index[0] != e["date"].replace("-", ""):
        raise LookupError(f"pead_price_raw lacks closes for {e['date']} or {nxt}")
    out = {"start": e["date"], "end": nxt}
    for arm in ("a0", "t1"):
        h = tilt.hold(e[arm], c)
        cost, turnover = tilt.trade_cost(prev[f"{arm}_end_weights"] if prev else None, e[arm])
        out |= {f"{arm}_gross": h["gross"], f"{arm}_cost": cost, f"{arm}_turnover": turnover,
                f"{arm}_net": (1 + h["gross"]) * (1 - cost) - 1, f"{arm}_end_weights": h["end_weights"],
                f"{arm}_artifacts": h["artifacts"], f"{arm}_missing": h["missing"]}
        stock_ret = pd.Series(h["stock_ret"])
    out["ic"] = tilt.rank_ic(pd.Series(e["q"]), stock_ret)
    (LOG / f"{e['date']}.result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def logs() -> tuple[dict, dict]:
    ev, res = {}, {}
    if LOG.exists():
        for f in sorted(LOG.glob("*.json")):
            (res if f.name.endswith(".result.json") else ev)[f.name[:10]] = json.loads(f.read_text())
    return ev, res


def run(today: date) -> list[str]:
    days = trading_days()
    done, results = logs()
    made = []
    for d, target in events().items():
        if d not in done and d <= today.isoformat():
            done[d] = record_event(d, target, days)
            made.append(f"event {d}")
    dates = sorted(done)
    for k, d in enumerate(dates[:-1]):
        if d in results:
            continue
        prev = results.get(dates[k - 1]) if k else None
        if k and prev is None:
            break  # results are chained (costs use the previous drift); wait in order
        try:
            results[d] = record_result(done[d], dates[k + 1], prev)
        except LookupError as exc:
            log.info("%s", exc)
            break
        made.append(f"result {d}")
    return made


def report() -> None:
    ev, res = logs()
    rows = [res[d] for d in sorted(res)]
    print(f"rebalances recorded {len(ev)}, completed periods {len(rows)} (stage 1 at {STAGE1}, stage 2 at {STAGE2})")
    if not rows:
        return
    import numpy as np
    for r in rows:
        print(f"  {r['start']} -> {r['end']}: A0 net {r['a0_net'] * 100:+.2f}%  T1 net {r['t1_net'] * 100:+.2f}%  "
              f"diff {(r['t1_net'] - r['a0_net']) * 100:+.2f}%p  IC {r['ic']:+.3f}  "
              f"turnover {r['a0_turnover']:.2f}/{r['t1_turnover']:.2f}")
    cum = {a: float(np.prod([1 + r[f'{a}_net'] for r in rows]) - 1) for a in ("a0", "t1")}
    ics = np.array([r["ic"] for r in rows])
    turn = sum(r["t1_turnover"] for r in rows) / max(sum(r["a0_turnover"] for r in rows), 1e-12)
    t = ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics))) if len(ics) > 1 and ics.std(ddof=1) > 0 else float("nan")
    print(f"cumulative net: A0 {cum['a0'] * 100:+.2f}%  T1 {cum['t1'] * 100:+.2f}%  diff {(cum['t1'] - cum['a0']) * 100:+.2f}%p | "
          f"IC mean {ics.mean():+.3f} (t {t:+.2f}) | turnover T1/A0 {turn:.2f}")
    if len(rows) >= STAGE1:
        s1 = rows[:STAGE1]
        missed = len([d for d in sorted(ev)[:STAGE1] if d not in res])
        c1 = len(s1) == STAGE1 and missed == 0
        t1r = sum(r["t1_turnover"] for r in s1) / max(sum(r["a0_turnover"] for r in s1), 1e-12)
        d6 = (np.prod([1 + r["t1_net"] for r in s1]) - np.prod([1 + r["a0_net"] for r in s1])) * 100
        ok = c1 and t1r <= 1.5 and d6 >= -2
        print(f"STAGE 1 (§80, first {STAGE1}): all scored {'P' if c1 else 'F'} | turnover ratio {t1r:.2f} "
              f"{'P' if t1r <= 1.5 else 'F'} | cumulative diff {d6:+.2f}%p {'P' if d6 >= -2 else 'F'} "
              f"=> {'운영 통과' if ok else '운영 실패'}")
    if len(rows) >= STAGE2:
        s2 = rows[:STAGE2]
        ic2 = np.mean([r["ic"] for r in s2])
        d24 = np.prod([1 + r["t1_net"] for r in s2]) - np.prod([1 + r["a0_net"] for r in s2])
        verdict = "백테스트와 일치" if ic2 > 0 and d24 > 0 else ("불일치" if ic2 < 0 and d24 < 0 else "판단 보류")
        print(f"STAGE 2 (§80, first {STAGE2}): IC mean {ic2:+.3f}, cumulative diff {d24 * 100:+.2f}%p => {verdict}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if sys.argv[1:2] == ["report"]:
        report()
    else:
        for m in run(datetime.now(market.KST).date()):
            print(m)
    sys.exit(0)
