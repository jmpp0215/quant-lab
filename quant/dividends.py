"""Distribution-event fetching with incremental SQLite caching.

The imperative-shell counterpart to momentum.py's dividend math: caches raw
per-symbol payout events (never a rolled-up "current yield", so callers can
correctly compute a point-in-time trailing yield for any historical date,
not just today - see momentum.trailing_yield()). Mirrors candles.py's
relationship to toss_client.py, but backed by storage.py's SQLite since the
data is tiny and sparse rather than dense daily bars.
"""

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from quant import config, kis_client, storage
from quant.kis_client import KisApiError

KST = ZoneInfo("Asia/Seoul")

# Candle history reaches back ~HISTORY_DAYS trading days (~14 months), and
# the earliest date backtest.py could ever simulate still needs its own
# LOOKBACK_MONTHS before that - self-documenting if those constants change.
BACKFILL_MONTHS = (config.HISTORY_DAYS // 21) + config.LOOKBACK_MONTHS + 1

# Re-query the last month on every incremental sync in case an event whose
# record_date already passed was entered into KIS's system late. Cheap (one
# extra month per API call) and made idempotent by dividend_events' PK.
OVERLAP_DAYS = 30

log = logging.getLogger(__name__)


def sync(conn, client: "kis_client.KisClient", symbol: str,
        today: date | None = None) -> None:
    """Bring one symbol's cached events up to date, at most once per day.

    First sync backfills the full BACKFILL_MONTHS window; later syncs only
    re-query from just before the last fetch, not the whole window again.
    """
    today = today or datetime.now(KST).date()
    state = storage.dividend_fetch_state(conn, symbol)
    if state is not None and date.fromisoformat(state) >= today:
        return

    if state is None:
        from_date = today - timedelta(days=31 * BACKFILL_MONTHS)
    else:
        from_date = date.fromisoformat(state) - timedelta(days=OVERLAP_DAYS)

    events = kis_client.dividend_events(
        client, symbol, from_date=from_date, to_date=today)

    now = datetime.now().astimezone().isoformat()
    storage.save_dividend_events(conn, symbol, events, now)
    storage.save_dividend_fetch_state(conn, symbol, today.isoformat(), now)


def sync_all(conn, client: "kis_client.KisClient", symbols,
            today: date | None = None) -> None:
    """sync() every symbol - one symbol's API failure must not blank out
    every other symbol's cached data for this run."""
    for sym in symbols:
        try:
            sync(conn, client, sym, today=today)
        except KisApiError as e:
            log.error("%s: dividend sync failed: %s", sym, e)


def load_all(conn, symbols) -> dict[str, list[dict]]:
    return storage.load_all_dividend_events(conn, list(symbols))
