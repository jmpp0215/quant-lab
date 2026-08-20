"""Record an external deposit or withdrawal.

    python cashflow.py 2026-08-14 3000000 "initial funding"
    python cashflow.py 2026-09-01 -500000 "withdrawal"
    python cashflow.py --account kis-isa 2026-08-14 3000000 "initial funding"

Neither Toss nor KIS expose transaction history through the API, so these
have to be entered by hand. Without them a deposit looks exactly like a
gain.
"""

import sys
from decimal import Decimal

from quant import accounts, storage


def main() -> int:
    account, rest = accounts.extract_account(sys.argv[1:])
    if len(rest) < 2:
        print(__doc__)
        return 1

    date = rest[0]
    amount = Decimal(rest[1])
    note = rest[2] if len(rest) > 2 else None
    kind = "opening_balance" if "--opening" in rest else "deposit"

    storage.init()
    with storage.connect() as conn:
        existing = conn.execute(
            "SELECT id, note FROM cashflows WHERE account = ? "
            "AND trade_date = ? AND amount = ?",
            (account, date, str(amount)),
        ).fetchall()

    if existing:
        # Re-running the same command is an easy mistake, and a duplicate
        # transfer silently corrupts every return figure that nets it out.
        print("\na matching entry already exists:")
        for row in existing:
            print(f"  id {row['id']}: {row['note'] or ''}")
        if input("record anyway? [yes/no]: ").strip().lower() != "yes":
            print("aborted")
            return 0

    with storage.connect() as conn:
        storage.save_cashflow(conn, date, account, amount, note, kind)
        rows = conn.execute(
            "SELECT trade_date, amount, note FROM cashflows "
            "WHERE account = ? ORDER BY trade_date", (account,),
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