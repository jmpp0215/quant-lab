"""Incremental daily refresh of pead_price_raw.

Unlike backfill_prices.py (a one-off, checkpointed full re-fetch of every
KOSPI ticker from 2020), this only pulls each symbol's data since its last
cached date - the same incremental pattern as quant/dividends.py's
sync()/sync_all() (per-symbol fetch-state row, small overlap window to catch
late corrections, one symbol's failure doesn't blank out the rest). Meant to
run daily via launchd ahead of the PBR strategy's rebalance, so
quant/stock_factors/portfolio_construction.py never sees week-old data.
"""

import logging
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent.parent))

import FinanceDataReader as fdr

from quant.pead.storage import (
    DB_PATH,
    init_db,
    price_fetch_state,
    save_price_fetch_state,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("refresh_prices")

# Re-fetch the last few days on every run in case a late correction lands -
# cheap (a handful of extra rows per ticker) and made idempotent by
# pead_price_raw's (date, symbol) primary key. Mirrors dividends.py's
# OVERLAP_DAYS.
OVERLAP_DAYS = 5

# First-ever sync for a symbol backfills from here, matching
# backfill_prices.py's original start date.
INITIAL_START_DATE = date(2020, 1, 1)


def sync_symbol(conn: sqlite3.Connection, symbol: str, today: date) -> None:
    """Bring one symbol's cached prices up to date, at most once per day."""
    today_key = today.strftime("%Y%m%d")
    state = price_fetch_state(conn, symbol)
    if state == today_key:
        return

    if state is None:
        start_date = INITIAL_START_DATE
    else:
        state_dt = date.fromisoformat(f"{state[:4]}-{state[4:6]}-{state[6:]}")
        start_date = state_dt - timedelta(days=OVERLAP_DAYS)

    df = fdr.DataReader(symbol, start_date.isoformat(), today.isoformat())
    if df.empty:
        return

    records = []
    for date_idx, row in df.iterrows():
        dt_str = date_idx.strftime("%Y%m%d")
        close_price = float(row["Close"])
        volume = float(row["Volume"])
        records.append((
            dt_str, symbol, float(row["Open"]), float(row["High"]),
            float(row["Low"]), close_price, volume, volume * close_price,
        ))

    conn.executemany(
        "INSERT OR REPLACE INTO pead_price_raw "
        "(date, symbol, open, high, low, close, volume, trading_value) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        records,
    )
    updated_at = datetime.now().astimezone().isoformat()
    save_price_fetch_state(conn, symbol, today_key, updated_at)


def main():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    today = datetime.now().astimezone().date()

    kospi_df = fdr.StockListing("KOSPI")
    tickers = kospi_df["Code"].tolist()
    log.info("refreshing prices for %d tickers up to %s", len(tickers), today)

    for i, ticker in enumerate(tickers, 1):
        try:
            sync_symbol(conn, ticker, today)
            conn.commit()
        except Exception as e:
            log.error("%s: refresh failed: %s", ticker, e)
        if i % 100 == 0:
            log.info("progress: %d/%d", i, len(tickers))

    conn.close()
    log.info("price refresh complete.")


if __name__ == "__main__":
    main()
