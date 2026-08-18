"""Record an external deposit or withdrawal.

    python cashflow.py 2026-08-14 3000000 "initial funding"
    python cashflow.py 2026-09-01 -500000 "withdrawal"

Toss does not expose transaction history through the API, so these have
to be entered by hand. Without them a deposit looks exactly like a gain.
"""

import sys
from decimal import Decimal

import storage

ACCOUNT = "toss-bot"


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1

    date = sys.argv[1]
    amount = Decimal(sys.argv[2])
    note = sys.argv[3] if len(sys.argv) > 3 else None

    storage.init()
    with storage.connect() as conn:
        storage.save_cashflow(conn, date, ACCOUNT, amount, note)

    direction = "deposit" if amount > 0 else "withdrawal"
    print(f"recorded {direction} of {abs(amount):,} KRW on {date}")

    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT trade_date, amount, note FROM cashflows "
            "WHERE account = ? ORDER BY trade_date", (ACCOUNT,),
        ).fetchall()

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