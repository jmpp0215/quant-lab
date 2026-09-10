"""Daily candle fetching with local caching.

Toss is the default source; KIS (source="kis") is available as an
alternative for the same domestic symbols. Either API caps each request at
100 candles and rate-limits aggressively, so we fetch once, store on disk,
and afterwards only pull the new days. KIS rows are normalised to the Toss
field names at fetch time, so the cache format and every downstream
consumer stay source-agnostic.
"""

import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from quant.toss_client import TossClient

KST = ZoneInfo("Asia/Seoul")
CACHE_DIR = Path(__file__).parent.parent / "data" / "candles"
PAGE_SIZE = 100
PAGE_DELAY = 1.0
# Calendar span per KIS request. The endpoint returns the newest 100
# trading rows within [end - this, end] regardless, so anything comfortably
# over 100 trading days works; this keeps each window a bit wider than that.
KIS_WINDOW_DAYS = 200

log = logging.getLogger(__name__)


def _cache_path(symbol: str, source: str = "toss") -> Path:
    base = CACHE_DIR if source == "toss" else CACHE_DIR / source
    return base / f"{symbol}.json"


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


def _kis_to_common(row: dict) -> dict:
    """One inquire-daily-itemchartprice output2 row -> the common candle
    shape (Toss field names), so the cache and downstream code do not care
    which source produced it. Only closePrice and timestamp[:10] are read
    anywhere; the rest are carried through verbatim."""
    d = row["stck_bsop_date"]  # YYYYMMDD
    return {
        "timestamp": f"{d[:4]}-{d[4:6]}-{d[6:]}T00:00:00.000+09:00",
        "openPrice": row["stck_oprc"],
        "highPrice": row["stck_hgpr"],
        "lowPrice": row["stck_lwpr"],
        "closePrice": row["stck_clpr"],
        "volume": row["acml_vol"],
        "tradingValue": row.get("acml_tr_pbmn", ""),
        "flngClsCode": row.get("flng_cls_code", ""),
    }


def fetch_kis(client, symbol: str, days: int = 260,
              today: date | None = None) -> list[dict]:
    """Fetch up to `days` daily candles from KIS, paging backwards by date
    window. inquire-daily-itemchartprice caps each call at 100 rows and has
    no cursor, so each page moves the end date to just before the oldest
    row already collected.
    """
    end = today or datetime.now(KST).date()
    out: list[dict] = []
    seen: set[str] = set()

    while len(out) < days:
        start = end - timedelta(days=KIS_WINDOW_DAYS)
        # adjusted=True: KIS 수정주가 tracks Toss's candle series to within a
        # won or two across 300 days of 102110 (verified live 2026-09-10);
        # 원주가 diverges by the cumulative distribution factor (~1.4% at
        # 15 months back). Matching Toss keeps the two sources drop-in
        # interchangeable for the strategy.
        resp = client.daily_chart(
            symbol, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"),
            adjusted=True)
        rows = [r for r in (resp.get("output2") or []) if r.get("stck_bsop_date")]
        if not rows:
            break

        fresh = [r for r in rows if r["stck_bsop_date"] not in seen]
        if not fresh:
            break
        seen.update(r["stck_bsop_date"] for r in fresh)
        out.extend(_kis_to_common(r) for r in fresh)

        if len(rows) < PAGE_SIZE:
            break  # a short page means no older data exists

        oldest = min(r["stck_bsop_date"] for r in rows)
        end = date(int(oldest[:4]), int(oldest[4:6]), int(oldest[6:])) \
            - timedelta(days=1)
        time.sleep(PAGE_DELAY)

    out.sort(key=_trading_date, reverse=True)
    log.info("%s: fetched %d candles from KIS", symbol, len(out))
    return out[:days]


def load_cached(symbol: str, source: str = "toss") -> list[dict]:
    path = _cache_path(symbol, source)
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save(symbol: str, candles: list[dict], source: str = "toss") -> None:
    path = _cache_path(symbol, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(candles, ensure_ascii=False), encoding="utf-8")


def get(client, symbol: str, days: int = 260,
        today: date | None = None,
        include_today: bool = False,
        source: str = "toss") -> list[dict]:
    """Return daily candles, newest first.

    Today's candle is excluded by default because it is still forming
    during market hours; the caller passes include_today=True once the
    session has closed and the candle is final.

    source="toss" (default) uses a TossClient; source="kis" uses a
    KisClient and the same symbol, cached separately under data/candles/kis
    so the two never mix.
    """
    today = today or datetime.now(KST).date()
    today_str = today.isoformat()

    def is_complete(c: dict) -> bool:
        d = _trading_date(c)
        return d < today_str or (include_today and d == today_str)
    cached = load_cached(symbol, source)
    complete = [c for c in cached if is_complete(c)]
    if len(complete) >= days:
        newest = _trading_date(complete[0])
        # When today's candle counts, the cache is only fresh if it holds
        # today; otherwise yesterday is enough. Without this the run right
        # after the close would keep serving the pre-close cache.
        required = today_str if include_today else (
            today - timedelta(days=1)).isoformat()
        if newest >= required:
            log.debug("%s: cache hit (%d candles)", symbol, len(complete))
            return complete[:days]

    if source == "kis":
        fresh = fetch_kis(client, symbol, days + 1, today=today)
    else:
        fresh = fetch(client, symbol, days + 1)
    save(symbol, fresh, source)
    return [c for c in fresh if is_complete(c)][:days]