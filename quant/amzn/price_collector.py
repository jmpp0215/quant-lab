"""AMZN daily price + valuation snapshot via KIS's overseas price-detail
endpoint (해외주식 현재가상세, tr_id HHDFS76200200).

Collection only - no signal/execution logic here. See quant/amzn/README.md
for auth requirements (this endpoint needs a live KIS account; a
paper-trading account cannot call it).

CLI:
    python -m quant.amzn.price_collector [--account kis-main] [--symbol AMZN]
"""
import argparse
import logging
import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from quant import accounts
from quant.amzn import storage
from quant.amzn.config import AmznConfig
from quant.amzn.rate_limit import RateLimiter

KST = ZoneInfo("Asia/Seoul")
log = logging.getLogger("amzn.price_collector")

# KIS field -> our column. Anything not in this map is dropped, not stored -
# config-agnostic here just means "store the documented valuation fields
# as-is", not "store the entire, KIS-version-dependent response blob".
FIELD_MAP = {
    "last": "last_price",
    "perx": "per",
    "pbrx": "pbr",
    "epsx": "eps",
    "bpsx": "bps",
    "tomv": "market_cap",
    "shar": "shares_outstanding",
    "tvol": "volume",
    "curr": "currency",
}


def _to_decimal_or_none(value) -> float | None:
    """Honest-failure parse: KIS returns numeric fields as strings, and an
    empty string or non-numeric junk means "not available", not zero."""
    if value is None or value == "":
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None


def parse_price_detail(output: dict) -> dict:
    """Map one price-detail API response's 'output' dict to our row shape.
    Missing/unparseable fields become None, never a silently-defaulted 0.
    """
    row = {}
    for kis_field, column in FIELD_MAP.items():
        raw = output.get(kis_field)
        row[column] = raw if column == "currency" else _to_decimal_or_none(raw)
    return row


def collect(client, symbol: str, config: AmznConfig,
            rate_limiter: RateLimiter | None = None) -> dict:
    """Fetch and parse one price-detail snapshot. Does not touch the DB -
    callers decide whether/how to persist (kept pure for testability)."""
    if rate_limiter:
        rate_limiter.wait()
    response = client.price_detail_overseas(symbol, exchange=config.exchange)
    output = response.get("output", {})
    return parse_price_detail(output)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Collect AMZN price/valuation snapshot")
    parser.add_argument("--account", default="kis-main",
                         help="KIS account to query with (needs a live, non-paper account)")
    parser.add_argument("--symbol", default="AMZN")
    args = parser.parse_args()

    storage.init_db()
    config = AmznConfig()
    client = accounts.get_client(args.account)
    rate_limiter = RateLimiter()

    now = datetime.now(KST)
    today_key = now.strftime("%Y%m%d")

    with sqlite3.connect(storage.DB_PATH) as conn:
        state = storage.price_fetch_state(conn, args.symbol)
        if state == today_key:
            log.info("%s already collected today (%s); skipping", args.symbol, today_key)
            return 0

        row = collect(client, args.symbol, config, rate_limiter)
        row["date"] = today_key
        row["symbol"] = args.symbol
        row["config_hash"] = config.get_hash()
        row["fetched_at"] = now.isoformat()

        storage.save_price_daily(conn, row)
        storage.save_price_fetch_state(conn, args.symbol, today_key, now.isoformat())

    log.info("saved %s price snapshot for %s: last=%s per=%s pbr=%s",
              args.symbol, today_key, row["last_price"], row["per"], row["pbr"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
