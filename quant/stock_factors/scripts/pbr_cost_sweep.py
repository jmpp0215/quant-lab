"""How much does the PBR Top-50 backtest give back once trading costs are
charged?

Same construction the scratch PBR backtests use: pead_price_raw closes,
bottom-20% PBR -> top 50 by point-in-time BPS (quant.stock_factors.value),
equal weight, full rebalance every N trading days, 2021-01-01 -> latest.

At each rebalance a cost of

    turnover_sell * (commission + slippage [+ transaction tax])
  + turnover_buy  * (commission + slippage)

is deducted from portfolio value that day. Slippage is swept; commission is
fixed (Toss domestic ~0.015%, the 0.014/0.015 difference is immaterial next
to slippage). The transaction-tax row is extra context, not part of the
original request - KOSPI 증권거래세 is ~0.15% one-way on sells in 2026.

Turnover is reported two ways, because they bracket reality:
  - set-churn  : only names entering/leaving the top-50 set trade. Lower
    bound - a deep-value PBR rank barely moves over 20 days (~4 names).
  - full true-up: weights drift with price between rebalances and every
    name is traded back to 1/N. Upper-ish bound, and closer to what the
    live diff actually does (2026-09-07 sent 50 orders to move ~4 names).
The honest cost figure sits between the two.

Read-only. Does not import quant/momentum.py, quant/strategy.py or
quant/allocation.py.

    python -m quant.stock_factors.scripts.pbr_cost_sweep
"""

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from quant.stock_factors.value import get_pbr_signal_func, prepare_pbr_signals

ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "data" / "quant.db"

TOP_N = 50
REBALANCE_DAYS = 20
SIM_START = "20210101"
TOP_PCT = 0.2

COMMISSION = 0.00015          # per side, Toss domestic
SLIPPAGE_GRID = [0.0, 0.001, 0.003, 0.005]
SELL_TAX = 0.0015             # KOSPI 증권거래세, one-way on sells, 2026


def load_prices() -> tuple[list[str], pd.DataFrame]:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        "SELECT date, symbol, close FROM pead_price_raw "
        "WHERE date >= '20200101'",
        conn,
    )
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    trading_dates = sorted(df["date"].dt.strftime("%Y%m%d").unique())
    pivot = df.pivot(index="date", columns="symbol", values="close").reindex(
        pd.to_datetime(trading_dates)
    )
    return trading_dates, pivot


def _turnover(w_now: dict[str, float], new: list[str], mode: str) -> tuple[float, float]:
    """(sell_fraction, buy_fraction) moving from current weights w_now to an
    equal-weight `new` set. set-churn ignores drift on names that stay."""
    tgt = {s: 1.0 / len(new) for s in new} if new else {}
    syms = set(w_now) | set(tgt)
    if mode == "set-churn":
        sell = sum(w_now[s] for s in w_now if s not in tgt)
        buy = sum(tgt[s] for s in tgt if s not in w_now)
        return sell, buy
    sell = sum(max(0.0, w_now.get(s, 0.0) - tgt.get(s, 0.0)) for s in syms)
    buy = sum(max(0.0, tgt.get(s, 0.0) - w_now.get(s, 0.0)) for s in syms)
    return sell, buy


def simulate(trading_dates, price_pivot, signal_func, slippage, sell_tax, mode):
    """Return (gross_total_ret, net_total_ret, sell_turnover_log)."""
    sim_dates = [d for d in trading_dates if d >= SIM_START]
    idx = pd.to_datetime(sim_dates)

    weights: dict[str, float] = {}   # per-symbol portfolio weight, drifts with price
    counter = 0
    gross = net = 1.0
    turnovers = []

    for i, date in enumerate(sim_dates):
        if i > 0 and weights:
            prev, cur = idx[i - 1], idx[i]
            grown = {}
            for sym, w in weights.items():
                r = 0.0
                if sym in price_pivot.columns:
                    p0 = price_pivot.at[prev, sym]
                    p1 = price_pivot.at[cur, sym]
                    if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                        r = (p1 - p0) / p0
                grown[sym] = w * (1 + r)
            day_ret = sum(grown.values()) - 1.0
            gross *= 1 + day_ret
            net *= 1 + day_ret
            tot = sum(grown.values())
            weights = {s: v / tot for s, v in grown.items()} if tot > 0 else grown

        if counter == 0 or i == 0:
            new = signal_func(date)[:TOP_N]
            if new:
                sell, buy = _turnover(weights, new, mode)
                cost = (sell * (COMMISSION + slippage + sell_tax)
                        + buy * (COMMISSION + slippage))
                net *= 1 - cost
                turnovers.append(sell)
                weights = {s: 1.0 / len(new) for s in new}
            counter = REBALANCE_DAYS
        counter -= 1

    return gross - 1.0, net - 1.0, turnovers


def main() -> int:
    print("loading prices...")
    trading_dates, price_pivot = load_prices()
    print("preparing point-in-time PBR signal...")
    df_bps = prepare_pbr_signals("20200101")
    signal_func = get_pbr_signal_func(df_bps, price_pivot, top_percentile=TOP_PCT)
    span_years = (pd.Timestamp(trading_dates[-1]) - pd.Timestamp(SIM_START)).days / 365.25

    gross, _, _ = simulate(trading_dates, price_pivot, signal_func, 0.0, 0.0, "full")
    print(f"\nPBR Top-{TOP_N}, {REBALANCE_DAYS}-day rebalance, equal weight")
    print(f"period : {SIM_START} -> {trading_dates[-1]}  (~{span_years:.1f}y)")
    print(f"gross total return (no costs): {gross * 100:+.1f}%  "
          f"({((1 + gross) ** (1 / span_years) - 1) * 100:+.1f}%/yr)")

    scenarios = [(f"slippage {s * 100:.1f}%", s, 0.0) for s in SLIPPAGE_GRID]
    scenarios += [(f"slippage {s * 100:.1f}% + STT {SELL_TAX * 100:.2f}%", s, SELL_TAX)
                  for s in SLIPPAGE_GRID if s]

    for mode in ("set-churn", "full"):
        _, _, turns = simulate(trading_dates, price_pivot, signal_func, 0.0, 0.0, mode)
        avg_turn = float(np.mean(turns[1:])) if len(turns) > 1 else 0.0
        print(f"\n--- turnover model: {mode}  "
              f"(avg one-way {avg_turn * 100:.0f}% / rebalance, "
              f"{len(turns)} rebalances) ---")
        print(f"  commission {COMMISSION * 100:.3f}%/side fixed")
        print(f"  {'scenario':<32} {'net ret':>9}  {'vs gross':>9}"
              f"  {'net CAGR':>9}  {'cost/reb':>8}")
        for label, slip, tax in scenarios:
            _, net, _ = simulate(trading_dates, price_pivot, signal_func,
                                 slip, tax, mode)
            rt = 2 * avg_turn * (COMMISSION + slip) + avg_turn * tax
            net_ann = (1 + net) ** (1 / span_years) - 1
            print(f"  {label:<32} {net * 100:>+8.1f}%  {(net - gross) * 100:>+8.1f}%p"
                  f"  {net_ann * 100:>+7.1f}%/yr  {rt * 100:>7.2f}%")

    print("\n  vs gross = total-return points given up over the whole period")
    print("  cost/reb = modelled round-trip cost per rebalance "
          "(2*turnover*(comm+slip) + turnover*tax)")
    print("  STT = KOSPI 증권거래세, ~0.15% one-way on sells (context only, "
          "not in original request)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
