"""Read-only views over the recorded history.

    python show.py                 latest signal and portfolio
    python show.py ranking         rank history per symbol
    python show.py variants        where alternatives diverged from live
    python show.py portfolio       value over time
    python show.py orders          trade history
    python show.py tranches        current sleeve books
    python show.py summary         every account's total/cash, latest date

    --account NAME   which account to show (default: toss-bot)
                     ignored by summary, which always shows every account
"""

import sys
import unicodedata
from decimal import Decimal

from quant import accounts, config, storage


def _name(symbol: str) -> str:
    """Resolve a symbol to a display name.

    Looks beyond the current universe: historical rows can reference a
    symbol that has since been retired to the watch list or dropped.
    """
    return (config.UNIVERSE.get(symbol)
            or config.WATCH_ONLY.get(symbol)
            or symbol)


def _width(text: str) -> int:
    """Terminal columns a string occupies.

    Hangul and other CJK glyphs take two columns, so padding by character
    count misaligns every row containing them.
    """
    return sum(
        2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
        for c in text
    )


def _pad(text: str, width: int) -> str:
    """Left-align `text` in `width` terminal columns, truncating if needed."""
    while _width(text) > width:
        text = text[:-1]
    return text + " " * max(0, width - _width(text))


def _pct(value: str | None) -> str:
    return f"{Decimal(value):>8.2%}" if value else "     n/a"


def latest(account: str) -> None:
    with storage.connect() as conn:
        run = conn.execute(
            "SELECT trade_date, updated_at, session FROM runs "
            "ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
        if not run:
            print("no runs recorded")
            return

        date = run["trade_date"]
        rows = conn.execute(
            "SELECT symbol, rank, momentum, selected FROM scores "
            "WHERE trade_date = ? ORDER BY rank", (date,)
        ).fetchall()
        pf = conn.execute(
            "SELECT total, cash FROM portfolio "
            "WHERE trade_date = ? AND account = ?", (date, account)
        ).fetchone()

    print(f"=== {date} (recorded {run['updated_at'][:19]}) ===\n")

    for r in rows:
        mark = "*" if r["selected"] else " "
        print(f" {mark}{r['rank']}. {_pad(_name(r['symbol']), 26)}"
              f"{_pct(r['momentum'])}")

    if pf:
        total = Decimal(pf["total"])
        cash = Decimal(pf["cash"])
        print(f"\n portfolio: {total:>12,.0f} KRW ({cash:,.0f} cash)")


def ranking(days: int = 30) -> None:
    """Rank per symbol over time, oldest column first."""
    with storage.connect() as conn:
        dates = [r["trade_date"] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM scores "
            "ORDER BY trade_date DESC LIMIT ?", (days,)
        ).fetchall()][::-1]

        if not dates:
            print("no history")
            return

        rows = conn.execute(
            "SELECT trade_date, symbol, rank, selected FROM scores "
            f"WHERE trade_date IN ({','.join('?' * len(dates))})", dates
        ).fetchall()

    grid: dict[str, dict[str, tuple[int, bool]]] = {}
    for r in rows:
        grid.setdefault(r["symbol"], {})[r["trade_date"]] = (
            r["rank"], bool(r["selected"]))

    header = "".join(d[5:].replace("-", "/").rjust(6) for d in dates)
    print(f"{_pad('', 26)}{header}")

    # Order by the most recent rank so the current standings read top-down.
    order = sorted(grid, key=lambda s: grid[s].get(dates[-1], (99, False))[0])

    for symbol in order:
        cells = ""
        for d in dates:
            entry = grid[symbol].get(d)
            if entry is None:
                cells += "     -"
            else:
                rank, selected = entry
                cells += f"{'*' if selected else ' '}{rank:>5}"
        print(f"{_pad(_name(symbol), 26)}{cells}")


def variants() -> None:
    """Where each alternative would have held something different."""
    with storage.connect() as conn:
        date = conn.execute(
            "SELECT MAX(trade_date) AS d FROM variants"
        ).fetchone()["d"]
        if not date:
            print("no variants recorded")
            return

        live = {r["symbol"] for r in conn.execute(
            "SELECT symbol FROM scores WHERE trade_date = ? AND selected = 1",
            (date,)
        ).fetchall()}

        rows = conn.execute(
            "SELECT variant, symbol, weight FROM variants "
            "WHERE trade_date = ? ORDER BY variant, symbol", (date,)
        ).fetchall()

    by_variant: dict[str, dict[str, Decimal]] = {}
    for r in rows:
        by_variant.setdefault(r["variant"], {})[r["symbol"]] = \
            Decimal(r["weight"])

    print(f"=== {date} ===\n")
    print(f"{_pad('live', 22)}"
          f"{', '.join(_name(s)[:12] for s in sorted(live))}\n")

    for name, weights in sorted(by_variant.items()):
        picks = ", ".join(
            f"{_name(s)[:12]} {w:.0%}"
            for s, w in sorted(weights.items(), key=lambda x: -x[1])
        )
        differs = "  <- different" if set(weights) != live else ""
        print(f"{_pad(name, 22)}{picks}{differs}")


def portfolio(account: str) -> None:
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT trade_date, total, cash FROM portfolio "
            "WHERE account = ? ORDER BY trade_date", (account,)
        ).fetchall()
        flows = conn.execute(
            "SELECT trade_date, amount, note FROM cashflows "
            "WHERE account = ? ORDER BY trade_date", (account,)
        ).fetchall()

    if not rows:
        print("no portfolio history")
        return

    deposits: dict[str, Decimal] = {}
    notes: dict[str, str] = {}
    for f in flows:
        date = f["trade_date"]
        deposits[date] = deposits.get(date, Decimal("0")) + Decimal(f["amount"])
        if f["note"]:
            notes[date] = f["note"]

    print(f"{'date':<12}{'total':>14}{'cash':>12}{'flow':>14}  note")
    for r in rows:
        flow = deposits.get(r["trade_date"])
        flow_text = f"{flow:>14,.0f}" if flow else " " * 14
        print(f"{r['trade_date']:<12}{Decimal(r['total']):>14,.0f}"
              f"{Decimal(r['cash']):>12,.0f}{flow_text}  "
              f"{notes.get(r['trade_date'], '')}")


def orders(account: str) -> None:
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT trade_date, tranche, symbol, side, quantity, "
            "filled_qty, avg_fill_price, commission FROM orders "
            "WHERE account = ? ORDER BY id", (account,)
        ).fetchall()

    if not rows:
        print("no orders")
        return

    print(f"{'date':<12}{'tr':>3} {_pad('symbol', 24)}{'side':<6}"
          f"{'qty':>6}{'filled':>8}{'price':>12}{'fee':>8}")

    for r in rows:
        tranche = "" if r["tranche"] is None else str(r["tranche"])
        price = Decimal(r["avg_fill_price"]) if r["avg_fill_price"] else 0
        print(f"{r['trade_date']:<12}{tranche:>3} {_pad(_name(r['symbol']), 24)}"
              f"{r['side']:<6}{r['quantity']:>6}{r['filled_qty'] or 0:>8}"
              f"{price:>12,.0f}{Decimal(r['commission'] or 0):>8,.0f}")


def tranches(account: str) -> None:
    with storage.connect() as conn:
        books = storage.load_all_tranche_holdings(conn, account)
        done = {r["tranche"] for r in conn.execute(
            "SELECT DISTINCT tranche FROM orders WHERE account = ? "
            "AND tranche IS NOT NULL "
            "AND trade_date >= date('now', 'start of month')", (account,)
        ).fetchall()}

    for t in config.TRANCHES:
        status = "done this month" if t in done else "pending"
        print(f"tranche {t} (trading day {t + 1}) - {status}")
        for symbol, quantity in sorted(books.get(t, {}).items()):
            print(f"  {_pad(_name(symbol), 26)}{quantity:>6}")
        print()


def summary() -> None:
    """Every account's total/cash on the latest date any of them has."""
    with storage.connect() as conn:
        date = conn.execute(
            "SELECT MAX(trade_date) AS d FROM portfolio"
        ).fetchone()["d"]
        if not date:
            print("no portfolio history")
            return

        rows = conn.execute(
            "SELECT account, currency, total, cash FROM portfolio "
            "WHERE trade_date = ?", (date,)
        ).fetchall()

    by_account = {r["account"]: r for r in rows}

    print(f"=== {date} ===\n")
    print(f"{_pad('account', 20)}{'total':>14}{'cash':>12}  currency")

    # Grouped by currency rather than assumed KRW: save_portfolio keeps
    # values in their native currency, so a summed total is only
    # meaningful within one currency.
    totals: dict[str, Decimal] = {}
    for account in accounts.ACCOUNTS:
        r = by_account.get(account)
        if r is None:
            print(f"{_pad(account, 20)}{'no data for ' + date:>26}")
            continue
        total, cash = Decimal(r["total"]), Decimal(r["cash"])
        totals[r["currency"]] = totals.get(r["currency"], Decimal("0")) + total
        print(f"{_pad(account, 20)}{total:>14,.0f}{cash:>12,.0f}  {r['currency']}")

    print()
    for currency, total in sorted(totals.items()):
        print(f"{_pad('total', 20)}{total:>14,.0f}  {currency}")


# ranking/variants/summary ignore account - ranking/variants are the
# shared signal, not per-account state, and summary always shows every
# account regardless of which one --account points at.
COMMANDS = {
    "latest": lambda account: latest(account),
    "ranking": lambda account: ranking(),
    "variants": lambda account: variants(),
    "portfolio": lambda account: portfolio(account),
    "orders": lambda account: orders(account),
    "tranches": lambda account: tranches(account),
    "summary": lambda account: summary(),
}


def main() -> int:
    account, rest = accounts.extract_account(sys.argv[1:])
    command = rest[0] if rest else "latest"
    handler = COMMANDS.get(command)
    if handler is None:
        print(__doc__)
        return 1
    handler(account)
    return 0


if __name__ == "__main__":
    sys.exit(main())