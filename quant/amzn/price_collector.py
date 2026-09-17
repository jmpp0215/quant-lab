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


def parse_holding(output1: list[dict], symbol: str = "AMZN") -> dict | None:
    """Extract `symbol`'s row from holdings_overseas()'s raw output1 list.
    None if not currently held (qty <= 0 or symbol absent).

    Field names verified live 2026-09-17 against a real CTRP6504R response
    for an AMZN position: avg_unpr3 (매입평균단가), evlu_pfls_rt1
    (평가손익율%), evlu_pfls_amt2 (평가손익금액, in the position's own
    currency - USD for AMZN), alongside the pdno/ccld_qty_smtl1/
    ovrs_now_pric1 fields quant.kis_client.snapshot_overseas() already
    reads from the same endpoint. Cross-checked by hand: avg_unpr3=218.9146,
    ovrs_now_pric1=245.96, qty=30 -> (245.96-218.9146)/218.9146*100 =
    12.35% and (245.96-218.9146)*30 = 811.36, matching evlu_pfls_rt1=12.35
    and evlu_pfls_amt2=811.35909 to within rounding.
    """
    for item in output1:
        if item.get("pdno") != symbol:
            continue
        qty = int(Decimal(item.get("ccld_qty_smtl1") or "0"))
        if qty <= 0:
            return None
        avg_price = _to_decimal_or_none(item.get("avg_unpr3"))
        current_price = _to_decimal_or_none(item.get("ovrs_now_pric1"))
        pnl_pct = _to_decimal_or_none(item.get("evlu_pfls_rt1"))
        pnl_usd = _to_decimal_or_none(item.get("evlu_pfls_amt2"))
        if pnl_pct is None and avg_price and current_price and avg_price != 0:
            pnl_pct = (current_price - avg_price) / avg_price * 100
        if pnl_usd is None and avg_price is not None and current_price is not None:
            pnl_usd = (current_price - avg_price) * qty
        return {
            "qty": qty,
            "avg_price": avg_price,
            "current_price": current_price,
            "unrealized_pnl_pct": pnl_pct,
            "unrealized_pnl_usd": pnl_usd,
        }
    return None


def collect_and_store(client, symbol: str, config: AmznConfig,
                       rate_limiter: RateLimiter | None = None,
                       now: datetime | None = None) -> dict | None:
    """Fetch, store, and return today's snapshot - gated to once per KST
    day via amzn_price_fetch_state. Returns None (does nothing) if already
    collected today. Factored out of main() so other callers (e.g.
    quant/amzn/scripts/check_amzn.py) reuse the exact same gate+store path
    instead of duplicating it."""
    now = now or datetime.now(KST)
    today_key = now.strftime("%Y%m%d")

    storage.init_db(storage.DB_PATH)
    with sqlite3.connect(storage.DB_PATH) as conn:
        state = storage.price_fetch_state(conn, symbol)
        if state == today_key:
            log.info("%s already collected today (%s); skipping", symbol, today_key)
            return None

        row = collect(client, symbol, config, rate_limiter)
        row["date"] = today_key
        row["symbol"] = symbol
        row["config_hash"] = config.get_hash()
        row["fetched_at"] = now.isoformat()

        storage.save_price_daily(conn, row)
        storage.save_price_fetch_state(conn, symbol, today_key, now.isoformat())

    return row


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Collect AMZN price/valuation snapshot")
    parser.add_argument("--account", default="kis-main",
                         help="KIS account to query with (needs a live, non-paper account)")
    parser.add_argument("--symbol", default="AMZN")
    args = parser.parse_args()

    config = AmznConfig()
    client = accounts.get_client(args.account)
    rate_limiter = RateLimiter()

    row = collect_and_store(client, args.symbol, config, rate_limiter)
    if row is None:
        return 0

    log.info("saved %s price snapshot for %s: last=%s per=%s pbr=%s",
              args.symbol, row["date"], row["last_price"], row["per"], row["pbr"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
