"""Reconcile each live PBR rebalance against what the backtest would have
assumed for it.

For every rebalance recorded in data/pbr_live.db this joins three things:
  1. the target portfolio saved at execution time (pbr_rebalance.target_portfolio_json)
  2. the actual orders, limits and fills (pbr_orders)
  3. that trading day's official close (data/quant.db pead_price_raw.close)

and reports, per rebalance and in aggregate:
  - selection -> order coverage (target symbols that never became an order)
  - fill outcome (filled / partial / unfilled, by side)
  - realised slippage of the average fill vs. our limit, and vs. the day's
    close (the price the backtest credits us with)

Read-only. Does not import or touch quant/momentum.py, quant/strategy.py or
quant/allocation.py.

    python -m quant.stock_factors.scripts.pbr_slippage
    python -m quant.stock_factors.scripts.pbr_slippage --date 2026-09-07
"""

import argparse
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
QUANT_DB = ROOT / "data" / "quant.db"
PBR_DB = ROOT / "data" / "pbr_live.db"


def _closes(trade_dates: set[str]) -> dict[tuple[str, str], Decimal]:
    """{(trade_date, symbol): close} for the given YYYY-MM-DD dates."""
    if not trade_dates:
        return {}
    keys = {d.replace("-", "") for d in trade_dates}
    back = {d.replace("-", ""): d for d in trade_dates}
    conn = sqlite3.connect(QUANT_DB)
    placeholders = ",".join("?" * len(keys))
    rows = conn.execute(
        f"SELECT date, symbol, close FROM pead_price_raw "
        f"WHERE date IN ({placeholders})",
        tuple(keys),
    ).fetchall()
    conn.close()
    out: dict[tuple[str, str], Decimal] = {}
    for date_key, symbol, close in rows:
        if close is None:
            continue
        out[(back[date_key], symbol)] = Decimal(str(close))
    return out


def _pct(x: Decimal) -> str:
    return f"{x * 100:+.3f}%"


def reconcile_date(conn: sqlite3.Connection, trade_date: str) -> None:
    reb = conn.execute(
        "SELECT target_n_stocks, assets_before, assets_after, "
        "target_portfolio_json, summary_json FROM pbr_rebalance "
        "WHERE trade_date = ?",
        (trade_date,),
    ).fetchone()

    orders = conn.execute(
        "SELECT symbol, side, qty, limit_price, filled_qty, avg_fill_price "
        "FROM pbr_orders WHERE trade_date = ? ORDER BY side, symbol",
        (trade_date,),
    ).fetchall()

    print(f"\n{'=' * 78}\n{trade_date}\n{'=' * 78}")

    if reb is None:
        print("  no pbr_rebalance row")
    else:
        target_n, assets_before, assets_after, target_json, summary_json = reb
        target = json.loads(target_json) if target_json else {}
        weights = {k: v for k, v in target.items() if isinstance(v, (int, float))}
        summary = json.loads(summary_json) if summary_json else {}

        if not weights:
            print(f"  target_portfolio_json has no weights ({target!r})")
            print("  -> this rebalance cannot be reconciled against a target")
        else:
            ordered_syms = {o[0] for o in orders}
            missing = sorted(set(weights) - ordered_syms)
            print(f"  target symbols                : {len(weights)}")
            print(f"  produced an order             : {len(set(weights) & ordered_syms)}")
            print(f"  no order row (held or dropped): {len(missing)}")
            if missing:
                print(f"    {', '.join(missing)}")
        if summary:
            print(f"  execution summary    : {summary}")

    if not orders:
        print("  no pbr_orders rows -> nothing to measure")
        return

    closes = _closes({trade_date})

    n_filled = n_partial = n_unfilled = 0
    tot_notional = Decimal(0)
    slip_vs_limit_krw = Decimal(0)
    slip_vs_close_krw = Decimal(0)
    close_covered_notional = Decimal(0)
    rows_out = []

    for symbol, side, qty, limit_price, filled_qty, avg_fill_price in orders:
        limit_p = Decimal(str(limit_price))
        filled_qty = filled_qty or 0
        if filled_qty == 0:
            n_unfilled += 1
            rows_out.append((side, symbol, qty, limit_p, None, closes.get((trade_date, symbol)), None, None, "UNFILLED"))
            continue
        if filled_qty < qty:
            n_partial += 1
            state = "partial"
        else:
            n_filled += 1
            state = "filled"

        avg_p = Decimal(str(avg_fill_price))
        q = Decimal(filled_qty)
        notional = limit_p * q
        tot_notional += notional

        # sign convention: positive = in our favour
        if side == "BUY":
            vs_limit = (limit_p - avg_p) / limit_p
        else:
            vs_limit = (avg_p - limit_p) / limit_p
        slip_vs_limit_krw += vs_limit * notional

        close = closes.get((trade_date, symbol))
        vs_close = None
        if close and close > 0:
            if side == "BUY":
                vs_close = (close - avg_p) / close
            else:
                vs_close = (avg_p - close) / close
            slip_vs_close_krw += vs_close * (avg_p * q)
            close_covered_notional += avg_p * q

        rows_out.append((side, symbol, qty, limit_p, avg_p, close, vs_limit, vs_close, state))

    print(f"\n  orders: {len(orders)}  "
          f"(filled {n_filled}, partial {n_partial}, unfilled {n_unfilled})")
    print(f"  {'':4} {'symbol':7} {'qty':>5} {'limit':>10} {'avgfill':>10} "
          f"{'close':>10} {'fill/limit':>11} {'fill/close':>11}  state")
    for side, symbol, qty, limit_p, avg_p, close, vs_limit, vs_close, state in rows_out:
        avg_s = f"{avg_p:>10.2f}" if avg_p is not None else f"{'-':>10}"
        close_s = f"{close:>10.2f}" if close is not None else f"{'-':>10}"
        vl_s = f"{_pct(vs_limit):>11}" if vs_limit is not None else f"{'-':>11}"
        vc_s = f"{_pct(vs_close):>11}" if vs_close is not None else f"{'-':>11}"
        print(f"  {side:4} {symbol:7} {qty:>5} {limit_p:>10.2f} {avg_s} "
              f"{close_s} {vl_s} {vc_s}  {state}")

    if tot_notional > 0:
        print(f"\n  notional-weighted fill vs limit : "
              f"{_pct(slip_vs_limit_krw / tot_notional)}  "
              f"({slip_vs_limit_krw:,.0f} KRW on {tot_notional:,.0f})")
    if close_covered_notional > 0:
        print(f"  notional-weighted fill vs close : "
              f"{_pct(slip_vs_close_krw / close_covered_notional)}  "
              f"({slip_vs_close_krw:,.0f} KRW on {close_covered_notional:,.0f})")
        print("  (fill vs close = gap between what we paid/received and the "
              "price the backtest credits)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="reconcile only this trade_date (YYYY-MM-DD)")
    args = ap.parse_args()

    if not PBR_DB.exists():
        print(f"no live DB at {PBR_DB}")
        return 1

    conn = sqlite3.connect(PBR_DB)
    if args.date:
        dates = [args.date]
    else:
        dates = [r[0] for r in conn.execute(
            "SELECT trade_date FROM pbr_rebalance ORDER BY trade_date").fetchall()]

    if not dates:
        print("no rebalances recorded in pbr_rebalance")
        return 0

    for d in dates:
        reconcile_date(conn, d)
    conn.close()
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
