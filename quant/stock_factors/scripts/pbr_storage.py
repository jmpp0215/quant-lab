import sqlite3
import json
from pathlib import Path
from datetime import datetime
from decimal import Decimal

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

def save_rebalance(conn: sqlite3.Connection, trade_date: str, target_n: int, 
                   assets_before: Decimal, assets_after: Decimal,
                   cash_before: Decimal, cash_after: Decimal,
                   target_portfolio: dict, summary: dict):
    now = datetime.now().astimezone().isoformat()
    conn.execute(
        """
        INSERT INTO pbr_rebalance 
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
