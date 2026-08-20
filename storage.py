"""SQLite persistence for signal history.

One row per trading date: reruns on the same date overwrite, since the
strategy only acts on completed daily candles and intraday recalculation
is noise for a monthly rebalance.
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "quant.db"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountSnapshot:
    """One broker account's end-of-day state, in the shape save_portfolio wants.
    
    Note: `currency` applies to `total` and `cash`. For overseas accounts, 
    individual items in `positions` may have their `price` in a different 
    currency (e.g. USD) depending on the broker's API behavior.
    """
    account: str
    currency: str
    total: Decimal
    cash: Decimal
    positions: list[dict]   # [{"symbol", "name", "qty", "price"}, ...]

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    trade_date  TEXT PRIMARY KEY,
    updated_at  TEXT NOT NULL,
    session     TEXT,
    ok          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scores (
    trade_date  TEXT NOT NULL REFERENCES runs(trade_date) ON DELETE CASCADE,
    symbol      TEXT NOT NULL,
    rank        INTEGER,
    momentum    TEXT,
    selected    INTEGER NOT NULL DEFAULT 0,
    weight      TEXT,
    PRIMARY KEY (trade_date, symbol)
);

CREATE TABLE IF NOT EXISTS indicators (
    trade_date  TEXT NOT NULL REFERENCES runs(trade_date) ON DELETE CASCADE,
    symbol      TEXT NOT NULL,
    name        TEXT NOT NULL,
    value       TEXT,
    PRIMARY KEY (trade_date, symbol, name)
);

CREATE TABLE IF NOT EXISTS portfolio (
    trade_date  TEXT NOT NULL,
    account     TEXT NOT NULL,
    currency    TEXT NOT NULL,
    total       TEXT NOT NULL,
    cash        TEXT NOT NULL,
    positions   TEXT NOT NULL,
    PRIMARY KEY (trade_date, account, currency)
);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY,
    trade_date      TEXT NOT NULL,
    placed_at       TEXT NOT NULL,
    account         TEXT NOT NULL,
    tranche         INTEGER,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    limit_price     TEXT NOT NULL,
    order_id        TEXT,
    filled          INTEGER NOT NULL DEFAULT 0,
    filled_qty      INTEGER,
    avg_fill_price  TEXT,
    commission      TEXT,
    tax             TEXT
);

CREATE TABLE IF NOT EXISTS variants (
    trade_date  TEXT NOT NULL REFERENCES runs(trade_date) ON DELETE CASCADE,
    variant     TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    weight      TEXT NOT NULL,
    PRIMARY KEY (trade_date, variant, symbol)
);

CREATE TABLE IF NOT EXISTS cashflows (
    id          INTEGER PRIMARY KEY,
    trade_date  TEXT NOT NULL,
    account     TEXT NOT NULL,
    amount      TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'deposit',
    note        TEXT
);
CREATE TABLE IF NOT EXISTS tranche_holdings (
    tranche     INTEGER NOT NULL,
    account     TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    quantity    INTEGER NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (tranche, account, symbol)
);

CREATE INDEX IF NOT EXISTS idx_cashflows_date ON cashflows(trade_date);

CREATE INDEX IF NOT EXISTS idx_scores_symbol ON scores(symbol, trade_date);
"""


@contextmanager
def connect(path: Path = DB_PATH):
    """Open a connection with foreign keys on, committing on clean exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init(path: Path = DB_PATH) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
    log.info("schema ready at %s", path)


def save_run(conn: sqlite3.Connection, trade_date: str, updated_at: str,
             session: str | None, ok: bool = True) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO runs (trade_date, updated_at, session, ok) "
        "VALUES (?, ?, ?, ?)",
        (trade_date, updated_at, session, int(ok)),
    )


def save_scores(conn: sqlite3.Connection, trade_date: str,
                rows: list[dict]) -> None:
    """rows: {symbol, rank, momentum, selected, weight}.

    Decimals are stored as text - SQLite REAL is a float and would
    reintroduce the precision problems Decimal exists to avoid.
    """
    conn.execute("DELETE FROM scores WHERE trade_date = ?", (trade_date,))
    conn.executemany(
        "INSERT INTO scores (trade_date, symbol, rank, momentum, selected, weight) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                trade_date,
                r["symbol"],
                r.get("rank"),
                str(r["momentum"]) if r.get("momentum") is not None else None,
                int(r.get("selected", False)),
                str(r["weight"]) if r.get("weight") is not None else None,
            )
            for r in rows
        ],
    )


def save_indicators(conn: sqlite3.Connection, trade_date: str,
                    rows: list[tuple[str, str, Decimal | None]]) -> None:
    """rows: (symbol, name, value)."""
    conn.execute("DELETE FROM indicators WHERE trade_date = ?", (trade_date,))
    conn.executemany(
        "INSERT INTO indicators (trade_date, symbol, name, value) "
        "VALUES (?, ?, ?, ?)",
        [(trade_date, sym, name, str(val) if val is not None else None)
         for sym, name, val in rows],
    )

def save_portfolio(conn: sqlite3.Connection, trade_date: str, account: str,
                   currency: str, total: Decimal, cash: Decimal,
                   positions: list[dict]) -> None:
    """Store one account's end-of-day snapshot.

    Values stay in their native currency: converting to KRW here would
    freeze the snapshot at one day's FX rate and make later recalculation
    impossible.
    """
    conn.execute(
        "INSERT OR REPLACE INTO portfolio "
        "(trade_date, account, currency, total, cash, positions) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (trade_date, account, currency, str(total), str(cash),
         json.dumps(positions, ensure_ascii=False)),
    )

def save_order(conn: sqlite3.Connection, trade_date: str, placed_at: str,
               account: str, symbol: str, side: str, quantity: int,
               limit_price: Decimal, order_id: str | None,
               filled: bool, execution: dict | None = None,
               tranche: int | None = None,
               executed_date: str | None = None) -> None:
    """Record one order, including fill details when the broker has them."""
    ex = execution or {}
    conn.execute(
        "INSERT INTO orders (trade_date, placed_at, account, tranche, symbol, "
        "side, quantity, limit_price, order_id, filled, filled_qty, "
        "avg_fill_price, commission, tax) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (trade_date, placed_at, account, tranche, symbol, side, quantity,
         str(limit_price), order_id, int(filled),
         int(ex["filledQuantity"]) if ex.get("filledQuantity") else None,
         ex.get("averageFilledPrice"),
         ex.get("commission"),
         ex.get("tax")),
    )
def tranches_done_this_month(conn: sqlite3.Connection, account: str,
                             trade_date: str) -> set[int]:
    """Tranches that have already rebalanced in the same calendar month.

    Drives the catch-up rule: a tranche that missed its scheduled day
    runs at the next opportunity instead of skipping the month.
    """
    rows = conn.execute(
        "SELECT DISTINCT tranche FROM orders "
        "WHERE account = ? AND trade_date LIKE ? AND tranche IS NOT NULL",
        (account, f"{trade_date[:7]}%"),
    ).fetchall()
    return {r["tranche"] for r in rows}

def rebalanced_this_month(conn: sqlite3.Connection, trade_date: str) -> bool:
    """True if any order was already placed in the same calendar month.

    This is how "first trading day of the month" is decided: rather than
    computing the calendar date, we treat the month's first run as the
    rebalance. A missed day simply shifts the rebalance later instead of
    skipping it.
    """
    month = trade_date[:7]
    row = conn.execute(
        "SELECT 1 FROM orders WHERE trade_date LIKE ? LIMIT 1",
        (f"{month}%",),
    ).fetchone()
    return row is not None


def ordered_today(conn: sqlite3.Connection, trade_date: str) -> bool:
    """Guard against a second rebalance on the same day."""
    row = conn.execute(
        "SELECT 1 FROM orders WHERE trade_date = ? LIMIT 1",
        (trade_date,),
    ).fetchone()
    return row is not None

def save_variants(conn: sqlite3.Connection, trade_date: str,
                  rows: list[tuple[str, str, Decimal]]) -> None:
    """rows: (variant, symbol, weight)."""
    conn.execute("DELETE FROM variants WHERE trade_date = ?", (trade_date,))
    conn.executemany(
        "INSERT INTO variants (trade_date, variant, symbol, weight) "
        "VALUES (?, ?, ?, ?)",
        [(trade_date, v, s, str(w)) for v, s, w in rows],
    )

def save_cashflow(conn: sqlite3.Connection, trade_date: str, account: str,
                  amount: Decimal, note: str | None = None,
                  kind: str = "deposit") -> None:
    """Record an external transfer, or the balance a strategy started from.

    The two are different things: a deposit changes the cash balance on
    that date, while an opening balance is a reference point that no
    transfer accompanied. Netting the latter out of cash reconciliation
    would make every trading day look like money had moved.
    """
    conn.execute(
        "INSERT INTO cashflows (trade_date, account, amount, kind, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (trade_date, account, str(amount), kind, note),
    )


def cashflows_by_date(conn: sqlite3.Connection,
                      account: str) -> dict[str, Decimal]:
    """Net external flow per date, for stripping out of return figures."""
    rows = conn.execute(
        "SELECT trade_date, SUM(CAST(amount AS REAL)) AS total "
        "FROM cashflows WHERE account = ? GROUP BY trade_date",
        (account,),
    ).fetchall()
    return {r["trade_date"]: Decimal(str(r["total"])) for r in rows}

def save_tranche_holdings(conn: sqlite3.Connection, tranche: int,
                          account: str, holdings: dict[str, int],
                          updated_at: str) -> None:
    """Replace one tranche's book. Symbols absent from `holdings` are
    dropped, so a tranche that exits a position leaves no stale row.
    """
    conn.execute(
        "DELETE FROM tranche_holdings WHERE tranche = ? AND account = ?",
        (tranche, account),
    )
    conn.executemany(
        "INSERT INTO tranche_holdings "
        "(tranche, account, symbol, quantity, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [(tranche, account, sym, qty, updated_at)
         for sym, qty in holdings.items() if qty > 0],
    )


def load_tranche_holdings(conn: sqlite3.Connection, tranche: int,
                          account: str) -> dict[str, int]:
    rows = conn.execute(
        "SELECT symbol, quantity FROM tranche_holdings "
        "WHERE tranche = ? AND account = ?",
        (tranche, account),
    ).fetchall()
    return {r["symbol"]: r["quantity"] for r in rows}


def load_all_tranche_holdings(conn: sqlite3.Connection,
                              account: str) -> dict[int, dict[str, int]]:
    rows = conn.execute(
        "SELECT tranche, symbol, quantity FROM tranche_holdings "
        "WHERE account = ?", (account,),
    ).fetchall()
    out: dict[int, dict[str, int]] = {}
    for r in rows:
        out.setdefault(r["tranche"], {})[r["symbol"]] = r["quantity"]
    return out

def unexplained_cash_change(conn: sqlite3.Connection, account: str,
                            trade_date: str) -> Decimal:
    """Cash movement that recorded trading does not account for.

    Cash moves for three reasons: fills, distributions, and external
    transfers. Netting out the fills leaves the other two, and a transfer
    is large enough to tell apart by size. Watching cash rather than total
    value avoids flagging every volatile session as a deposit.
    """
    rows = conn.execute(
        "SELECT trade_date, cash FROM portfolio WHERE account = ? "
        "AND trade_date <= ? ORDER BY trade_date DESC LIMIT 2",
        (account, trade_date),
    ).fetchall()
    if len(rows) < 2:
        return Decimal("0")

    today = Decimal(rows[0]["cash"])
    previous = Decimal(rows[1]["cash"])

    fills = conn.execute(
        "SELECT side, filled_qty, avg_fill_price, commission, tax "
        "FROM orders WHERE account = ? AND executed_date = ? AND filled = 1",
        (account, trade_date),
    ).fetchall()

    traded = Decimal("0")
    for f in fills:
        if not f["filled_qty"] or not f["avg_fill_price"]:
            continue
        notional = Decimal(str(f["filled_qty"])) * Decimal(f["avg_fill_price"])
        traded += notional if f["side"] == "SELL" else -notional
        traded -= Decimal(f["commission"] or 0)
        traded -= Decimal(f["tax"] or 0)

    recorded = conn.execute(
        "SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) AS total "
        "FROM cashflows WHERE account = ? AND trade_date = ? "
        "AND kind = 'deposit'",
        (account, trade_date),
    ).fetchone()["total"]

    return today - previous - traded - Decimal(str(recorded))
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    init()