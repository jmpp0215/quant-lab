import sqlite3
import json
import logging
from pathlib import Path
from datetime import datetime
from decimal import Decimal

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "pbr_live.db"

# 부분/실패 리밸런싱 이후, 실패한 종목만 소규모로 재시도할 수 있는 기간(거래일). 이
# 기간을 넘기면 재시도를 포기하고 다음 20거래일 전체 사이클을 기다린다 - 몇 종목만
# 매번 체결이 안 되는 패턴이 반복돼도 전체 포트폴리오가 매일 재구성되는 걸 막기 위한
# 상한이다. check_pbr_due(자동 스케줄)와 run_pbr_live --retry(수동 실행 포함) 둘 다
# 이 값을 쓴다 - 여기 두는 건 check_pbr_due가 import 시 logging.basicConfig를 호출해
# run_pbr_live에서 직접 import하기 곤란하기 때문.
RETRY_WINDOW_TRADING_DAYS = 3

# 전체 리밸런싱 주기(거래일). check_pbr_due가 쓰고, daily.py가 트래커용 다음
# 리밸런싱 예정일을 계산할 때도 쓴다 - 위와 같은 이유로 여기 둔다.
FULL_REBALANCE_TRADING_DAYS = 20

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
        # Record-only disclosure flags (quant/stock_factors/disclosures.py):
        # one row per filing seen for a rebalance's target symbol, never
        # read by the trading path.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS disclosure_flags (
                trade_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                rcept_no TEXT NOT NULL,
                rcept_dt TEXT NOT NULL,
                report_nm TEXT NOT NULL,
                title_class TEXT NOT NULL,
                llm_severe INTEGER,
                llm_category TEXT,
                llm_reason TEXT,
                llm_model TEXT,
                PRIMARY KEY (trade_date, symbol, rcept_no)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS disclosure_runs (
                trade_date TEXT PRIMARY KEY,
                ran_at TEXT NOT NULL,
                n_symbols INTEGER,
                n_filings INTEGER,
                n_llm_calls INTEGER,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cost_usd TEXT,
                errors INTEGER
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

def get_rebalance_target(conn: sqlite3.Connection, trade_date: str) -> dict | None:
    """target_portfolio of the rebalance on trade_date, or None."""
    row = conn.execute(
        "SELECT target_portfolio_json FROM pbr_rebalance WHERE trade_date = ?",
        (trade_date,)).fetchone()
    if row is None or not row["target_portfolio_json"]:
        return None
    return json.loads(row["target_portfolio_json"])

def disclosure_run_exists(conn: sqlite3.Connection, trade_date: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM disclosure_runs WHERE trade_date = ?", (trade_date,)
    ).fetchone() is not None

def save_disclosure_run(conn: sqlite3.Connection, trade_date: str,
                        rows: list[dict], stats: dict) -> None:
    """rows: {symbol, rcept_no, rcept_dt, report_nm, title_class,
    llm_severe, llm_category, llm_reason, llm_model}. Replaces any earlier
    run for the date, so a re-run never leaves a mix of two runs' rows."""
    conn.execute("DELETE FROM disclosure_flags WHERE trade_date = ?", (trade_date,))
    conn.executemany(
        """
        INSERT INTO disclosure_flags
        (trade_date, symbol, rcept_no, rcept_dt, report_nm, title_class,
         llm_severe, llm_category, llm_reason, llm_model)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(trade_date, r["symbol"], r["rcept_no"], r["rcept_dt"], r["report_nm"],
          r["title_class"],
          None if r["llm_severe"] is None else int(r["llm_severe"]),
          r["llm_category"], r["llm_reason"], r["llm_model"]) for r in rows])
    conn.execute(
        """
        INSERT OR REPLACE INTO disclosure_runs
        (trade_date, ran_at, n_symbols, n_filings, n_llm_calls, input_tokens,
         output_tokens, cost_usd, errors)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (trade_date, datetime.now().astimezone().isoformat(), stats["n_symbols"],
         len(rows), stats["n_llm_calls"], stats["input_tokens"],
         stats["output_tokens"], str(stats["cost_usd"]), stats["errors"]))

if __name__ == "__main__":
    init()
