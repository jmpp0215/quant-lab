"""Daily candle fetching with local caching.

The API caps each request at 100 candles and rate-limits aggressively, so
we fetch once, store on disk, and afterwards only pull the new days.
"""

import json
import logging
import time
from datetime import date, datetime
from pathlib import Path
from datetime import date, datetime, timedelta
from toss_client import TossClient

CACHE_DIR = Path(__file__).parent / "data" / "candles"
PAGE_SIZE = 100
PAGE_DELAY = 1.0

log = logging.getLogger(__name__)


def _cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol}.json"


def _trading_date(candle: dict) -> str:
    """Return the YYYY-MM-DD trading date of a candle.

    US candles are timestamped at local midnight converted to KST, so the
    date portion of the string is already the correct trading day.
    """
    return candle["timestamp"][:10]


def fetch(client: TossClient, symbol: str, days: int = 260) -> list[dict]:
    """Fetch up to `days` daily candles, paging backwards as needed."""
    out: list[dict] = []
    before: str | None = None

    while len(out) < days:
        params = {"symbol": symbol, "interval": "1d", "limit": PAGE_SIZE}
        if before:
            params["before"] = before

        result = client.get("/api/v1/candles", params=params)["result"]
        page = result["candles"]
        if not page:
            break

        out.extend(page)
        before = result.get("nextBefore")
        if not before:
            break

        time.sleep(PAGE_DELAY)

    log.info("%s: fetched %d candles", symbol, len(out))
    return out[:days]


def load_cached(symbol: str) -> list[dict]:
    path = _cache_path(symbol)
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save(symbol: str, candles: list[dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(symbol).write_text(
        json.dumps(candles, ensure_ascii=False), encoding="utf-8"
    )


def get(client: TossClient, symbol: str, days: int = 260,
        today: date | None = None) -> list[dict]:
    """Return daily candles, newest first, excluding today's partial candle.

    Uses the disk cache when it already covers the requested range, so a
    daily run only costs one request per symbol instead of three.
    """
    today = today or datetime.now().date()
    today_str = today.isoformat()

    cached = load_cached(symbol)
    complete = [c for c in cached if _trading_date(c) < today_str]

    if len(complete) >= days:
        newest = _trading_date(complete[0])
        # The cache is only good if it already holds yesterday's candle.
        # A wider window silently serves stale data on the very day a
        # rebalance depends on it.
        if newest >= (today - timedelta(days=1)).isoformat():
            log.debug("%s: cache hit (%d candles)", symbol, len(complete))
            return complete[:days]

    fresh = fetch(client, symbol, days + 1)
    save(symbol, fresh)
    return [c for c in fresh if _trading_date(c) < today_str][:days]