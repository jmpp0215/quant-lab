"""Read-only view of recorded disclosure flags (candidate A).

    python quant/stock_factors/scripts/show_disclosures.py              latest recorded rebalance
    python quant/stock_factors/scripts/show_disclosures.py 2026-10-16
    python quant/stock_factors/scripts/show_disclosures.py --all        every symbol, not just flagged
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3]))

from quant.stock_factors import disclosures  # noqa: E402
from quant.stock_factors.scripts import pbr_storage  # noqa: E402


def mark(v):
    return {True: "●", False: "·", None: "?"}[v]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="?")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    with pbr_storage.connect() as conn:
        run = conn.execute(
            "SELECT * FROM disclosure_runs WHERE trade_date = COALESCE(?, "
            "(SELECT MAX(trade_date) FROM disclosure_runs))", (args.date,)).fetchone()
        if run is None:
            print("no disclosure run recorded" + (f" for {args.date}" if args.date else ""))
            return 1
        rows = conn.execute(
            "SELECT * FROM disclosure_flags WHERE trade_date = ? ORDER BY symbol, rcept_dt DESC",
            (run["trade_date"],)).fetchall()

    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r["symbol"]].append({**dict(r), "llm_severe": None if r["llm_severe"] is None
                                    else bool(r["llm_severe"])})
    flags = {s: disclosures.symbol_flags(rs) for s, rs in by_sym.items()}
    count = {k: sum(f[k] is True for f in flags.values()) for k in ("R1", "R2", "L")}

    print(f"{run['trade_date']}  symbols {run['n_symbols']}, filings {run['n_filings']}, "
          f"flagged R1={count['R1']} R2={count['R2']} L={count['L']}, "
          f"LLM calls {run['n_llm_calls']}, cost ${float(run['cost_usd']):.3f}, errors {run['errors']}")
    print("● flagged  · not flagged  ? LLM unjudged\n")

    for sym in sorted(flags, key=lambda s: (flags[s]["L"] is not True, not flags[s]["R1"], s)):
        f = flags[sym]
        if not args.all and f["L"] is not True:
            continue
        print(f"{sym}  R1 {mark(f['R1'])}  R2 {mark(f['R2'])}  L {mark(f['L'])}")
        for r in by_sym[sym]:
            if r["title_class"] == "severe":
                verdict = "제목만으로 중대"
            elif r["title_class"] == "review":
                verdict = {True: "LLM 중대", False: "LLM 해당없음", None: "LLM 미판정"}[r["llm_severe"]]
                if r["llm_reason"]:
                    verdict += f" — {r['llm_reason']}"
            else:
                continue
            print(f"    {r['rcept_dt']}  {disclosures.base_title(r['report_nm'])}  [{verdict}]")
        print()
    if not args.all:
        print(f"(L 미표시 {len(flags) - count['L']}종목은 --all 로 확인)")
    print(f"(기간 내 공시 0건 {run['n_symbols'] - len(flags)}종목은 표시 대상 아님)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
