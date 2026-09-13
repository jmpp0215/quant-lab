import sqlite3
import json
import logging
from pathlib import Path
from datetime import datetime
from decimal import Decimal

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "pbr_live.db"

def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init():
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pbr_rebalance (
                trade_date TEXT PRIMARY KEY,
                executed_at TEXT,
                target_n_stocks INTEGER,
                assets_before TEXT,
                assets_after TEXT,
                cash_before TEXT,
                cash_after TEXT,
                target_portfolio_json TEXT,
                summary_json TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pbr_orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT,
                symbol TEXT,
                side TEXT,
                qty INTEGER,
                limit_price TEXT,
                filled_qty INTEGER,
                avg_fill_price TEXT,
                broker_order_id TEXT
            )
        """)

def get_latest_rebalance_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT trade_date FROM pbr_rebalance ORDER BY trade_date DESC LIMIT 1").fetchone()
    return row["trade_date"] if row else None

def get_latest_rebalance(conn: sqlite3.Connection) -> dict | None:
    """Full detail on the most recent rebalance row (by trade_date), parsed
    out of its JSON columns - {trade_date, all_clear, failed, partial,
    retry_of, target_portfolio}. Used to decide whether a retry is needed
    (all_clear) and, when retrying, to replay the same target instead of
    recomputing the PBR ranking. `retry_of` is the trade_date of the
    original full rebalance this row retried, or None for a full rebalance
    itself (see save_rebalance's summary dict)."""
    row = conn.execute(
        "SELECT trade_date, summary_json, target_portfolio_json FROM pbr_rebalance "
        "ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    summary = json.loads(row["summary_json"]) if row["summary_json"] else {}
    target_portfolio = json.loads(row["target_portfolio_json"]) if row["target_portfolio_json"] else {}
    return {
        "trade_date": row["trade_date"],
        "all_clear": bool(summary.get("all_clear", False)),
        "failed": summary.get("failed", []),
        "partial": summary.get("partial", []),
        "retry_of": summary.get("retry_of"),
        "target_portfolio": target_portfolio,
    }

def get_latest_full_rebalance_date(conn: sqlite3.Connection) -> str | None:
    """Most recent trade_date that was a full portfolio reconstruction (i.e.
    NOT a retry of an earlier rebalance - summary_json has no "retry_of").
    This is what the 20-trading-day full-cycle gate paces off of, so that
    retry attempts (see get_latest_rebalance) don't push the next full
    rebalance later."""
    rows = conn.execute(
        "SELECT trade_date, summary_json FROM pbr_rebalance ORDER BY trade_date DESC"
    ).fetchall()
    for row in rows:
        summary = json.loads(row["summary_json"]) if row["summary_json"] else {}
        if not summary.get("retry_of"):
            return row["trade_date"]
    return None

def save_rebalance(conn: sqlite3.Connection, trade_date: str, target_n: int,
                   assets_before: Decimal, assets_after: Decimal,
                   cash_before: Decimal, cash_after: Decimal,
                   target_portfolio: dict, summary: dict):
    # INSERT OR REPLACE (not plain INSERT): trade_date is the PRIMARY KEY, so an
    # operator re-running run_pbr_live.py for the same day (e.g. to retry a
    # failed rebalance) used to crash on the second save with
    # sqlite3.IntegrityError *after* real orders had already been placed -
    # and since this call sits in the same `with connect() as conn:` block as
    # that attempt's save_order calls, the crash rolled back the whole
    # transaction, silently losing the audit trail for orders that had
    # actually executed. A same-day re-run now overwrites this summary row
    # with the latest attempt; pbr_orders is unaffected (AUTOINCREMENT PK),
    # so every attempt's individual order rows still accumulate.
    existing = conn.execute(
        "SELECT 1 FROM pbr_rebalance WHERE trade_date = ?", (trade_date,)
    ).fetchone()
    if existing:
        log.warning("overwriting existing pbr_rebalance row for %s (same-day re-run)", trade_date)
    now = datetime.now().astimezone().isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO pbr_rebalance
        (trade_date, executed_at, target_n_stocks, assets_before, assets_after, cash_before, cash_after, target_portfolio_json, summary_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (trade_date, now, target_n, str(assets_before), str(assets_after), str(cash_before), str(cash_after),
         json.dumps(target_portfolio), json.dumps(summary))
    )

def save_order(conn: sqlite3.Connection, trade_date: str, symbol: str, side: str, 
               qty: int, limit_price: Decimal, filled_qty: int, avg_fill_price: Decimal, 
               broker_order_id: str | None):
    conn.execute(
        """
        INSERT INTO pbr_orders
        (trade_date, symbol, side, qty, limit_price, filled_qty, avg_fill_price, broker_order_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (trade_date, symbol, side, qty, str(limit_price), filled_qty, str(avg_fill_price) if avg_fill_price else None, broker_order_id)
    )

if __name__ == "__main__":
    init()
