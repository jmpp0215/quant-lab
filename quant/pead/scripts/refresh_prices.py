"""Incremental daily refresh of pead_price_raw.

Unlike backfill_prices.py (a one-off, checkpointed full re-fetch of every
KOSPI ticker from 2020), this only pulls each symbol's data since its last
cached date - the same incremental pattern as quant/dividends.py's
sync()/sync_all() (per-symbol fetch-state row, small overlap window to catch
late corrections, one symbol's failure doesn't blank out the rest). Meant to
run daily via launchd ahead of the PBR strategy's rebalance, so
quant/stock_factors/portfolio_construction.py never sees week-old data.

Source is KIS 국내주식기간별시세 for the KRX market (J), 원주가 - the
official KRX close. It used to be FinanceDataReader's default (Naver
fchart), which from 2026-09-14 started reporting the integrated
(KRX + after-hours) close instead: ~80% of symbols a day differed from
the KRX close by ~1% on average, sometimes outside the KRX day's range,
while volume stayed KRX's (RESEARCH_LOG section 24). Naver also had no
request timeout, which hung a run for 22 hours on 2026-10-05.

Today's bar is never stored: launchd runs this at 12:30, mid-session, and a
mid-session bar is a partial one (2026-10-01's rows kept their 12:30 close
and volume because the next two runs failed). The next run's overlap
window picks it up once it is final.

The symbol list is the symbols already tracked in pead_price_fetch_state,
not a live KRX listing - fdr.StockListing("KOSPI") failed with a 404 on
2026-10-02 and took the whole run down with it. A newly listed stock is not
added automatically; backfill_prices.py is how the universe grows.
"""

import logging
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from quant.kis_client import KisClient  # noqa: E402
from quant.pead.storage import (  # noqa: E402
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
# OVERLAP_DAYS. Also what replaces a day that was skipped as "today".
OVERLAP_DAYS = 5

# First-ever sync for a symbol backfills from here, matching
# backfill_prices.py's original start date.
INITIAL_START_DATE = date(2020, 1, 1)

# The endpoint returns at most 100 rows per call and has no cursor, so a
# long range is walked backwards in calendar windows. A 131-day inclusive
# window holds at most 95 weekdays, so it is never silently truncated.
WINDOW_DAYS = 130
PAGE_DELAY = 0.06


def fetch_daily(client: KisClient, symbol: str, start: date,
                end: date) -> list[tuple]:
    """KRX daily bars (원주가) for start..end inclusive, as
    pead_price_raw rows (date, symbol, open, high, low, close, volume,
    trading_value)."""
    rows: dict[str, tuple] = {}
    window_end = end
    while window_end >= start:
        window_start = max(start, window_end - timedelta(days=WINDOW_DAYS))
        resp = client.daily_chart(symbol, window_start.strftime("%Y%m%d"),
                                  window_end.strftime("%Y%m%d"), adjusted=False)
        for r in resp.get("output2") or []:
            d = r.get("stck_bsop_date")
            if not d or not (start.strftime("%Y%m%d") <= d <= end.strftime("%Y%m%d")):
                continue
            close, volume = float(r["stck_clpr"]), float(r["acml_vol"])
            rows[d] = (d, symbol, float(r["stck_oprc"]), float(r["stck_hgpr"]),
                       float(r["stck_lwpr"]), close, volume, volume * close)
        window_end = window_start - timedelta(days=1)
        time.sleep(PAGE_DELAY)
    return [rows[d] for d in sorted(rows)]


def sync_symbol(conn: sqlite3.Connection, client: KisClient, symbol: str,
                today: date) -> None:
    """Bring one symbol's cached prices up to yesterday, at most once per day."""
    today_key = today.strftime("%Y%m%d")
    state = price_fetch_state(conn, symbol)
    if state == today_key:
        return

    if state is None:
        start_date = INITIAL_START_DATE
    else:
        state_dt = date.fromisoformat(f"{state[:4]}-{state[4:6]}-{state[6:]}")
        start_date = state_dt - timedelta(days=OVERLAP_DAYS)

    # Up to yesterday only - see the module docstring on today's bar.
    records = fetch_daily(client, symbol, start_date, today - timedelta(days=1))
    if not records:
        return

    conn.executemany(
        "INSERT OR REPLACE INTO pead_price_raw "
        "(date, symbol, open, high, low, close, volume, trading_value) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        records,
    )
    updated_at = datetime.now().astimezone().isoformat()
    save_price_fetch_state(conn, symbol, today_key, updated_at)


def tracked_symbols(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT symbol FROM pead_price_fetch_state ORDER BY symbol")]


def main():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    today = datetime.now().astimezone().date()
    client = KisClient("isa")

    tickers = tracked_symbols(conn)
    log.info("refreshing prices for %d tickers up to %s (exclusive)",
             len(tickers), today)

    for i, ticker in enumerate(tickers, 1):
        try:
            sync_symbol(conn, client, ticker, today)
            conn.commit()
        except Exception as e:
            log.error("%s: refresh failed: %s", ticker, e)
        if i % 100 == 0:
            log.info("progress: %d/%d", i, len(tickers))

    conn.close()
    log.info("price refresh complete.")


if __name__ == "__main__":
    main()
