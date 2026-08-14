"""SQLite persistence for signal history.

One row per trading date: reruns on the same date overwrite, since the
strategy only acts on completed daily candles and intraday recalculation
is noise for a monthly rebalance.
"""

import logging
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
import json
DB_PATH = Path(__file__).parent / "data" / "quant.db"

log = logging.getLogger(__name__)

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

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    init()