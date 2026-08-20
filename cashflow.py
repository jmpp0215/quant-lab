"""Record an external deposit or withdrawal.

    python cashflow.py 2026-08-14 3000000 "initial funding"
    python cashflow.py 2026-09-01 -500000 "withdrawal"

Toss does not expose transaction history through the API, so these have
to be entered by hand. Without them a deposit looks exactly like a gain.
"""

import sys
from decimal import Decimal

from quant import storage

ACCOUNT = "toss-bot"


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1

    date = sys.argv[1]
    amount = Decimal(sys.argv[2])
    note = sys.argv[3] if len(sys.argv) > 3 else None
    kind = "opening_balance" if "--opening" in sys.argv else "deposit"

    storage.init()
    with storage.connect() as conn:
        existing = conn.execute(
            "SELECT id, note FROM cashflows WHERE account = ? "
            "AND trade_date = ? AND amount = ?",
            (ACCOUNT, date, str(amount)),
        ).fetchall()

    if existing:
        # Re-running the same command is an easy mistake, and a duplicate
        # transfer silently corrupts every return figure that nets it out.
        print(f"\na matching entry already exists:")
        for row in existing:
            print(f"  id {row['id']}: {row['note'] or ''}")
        if input("record anyway? [yes/no]: ").strip().lower() != "yes":
            print("aborted")
            return 0

    with storage.connect() as conn:
        storage.save_cashflow(conn, date, ACCOUNT, amount, note, kind)

    print("\nall cashflows:")
    total = Decimal("0")
    for r in rows:
        total += Decimal(r["amount"])
        print(f"  {r['trade_date']}  {Decimal(r['amount']):>12,}  "
              f"{r['note'] or ''}")
    print(f"  {'net':>10}  {total:>12,}")

    return 0


if __name__ == "__main__":
    sys.exit(main())