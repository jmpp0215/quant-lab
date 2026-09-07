"""AMZN headline collection from KIS's 해외뉴스종합(제목) and 해외속보(제목)
endpoints, with LLM event-tagging frozen at collection time (see
quant/amzn/llm_tagger.py's docstring for why - never re-tagged, never used
for signal generation).

Collection only - no signal/execution logic here.

CLI:
    python -m quant.amzn.news_collector [--account kis-main] [--symbol AMZN] [--skip-tagging]
"""
import argparse
import logging
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from quant import accounts
from quant.amzn import llm_tagger, storage
from quant.amzn.config import AmznConfig
from quant.amzn.rate_limit import RateLimiter

KST = ZoneInfo("Asia/Seoul")
log = logging.getLogger("amzn.news_collector")


def _published_at(data_dt: str, data_tm: str) -> str:
    data_tm = (data_tm or "").rjust(6, "0")[:6]
    if len(data_dt) == 8 and data_tm.isdigit():
        return f"{data_dt[:4]}-{data_dt[4:6]}-{data_dt[6:]}T{data_tm[:2]}:{data_tm[2:4]}:{data_tm[4:]}"
    return data_dt or ""


def parse_news_title_rows(outblock1: list[dict], symbol: str, fetched_at: str) -> list[dict]:
    rows = []
    for item in outblock1:
        headline = (item.get("title") or "").strip()
        news_key = item.get("news_key")
        if not headline or not news_key:
            continue  # honest skip - no fabricated key/headline
        rows.append({
            "source_api": "news_title",
            "news_key": news_key,
            "symbol": symbol,
            "headline": headline,
            "published_at": _published_at(item.get("data_dt", ""), item.get("data_tm", "")),
            "source": item.get("source"),
            "fetched_at": fetched_at,
        })
    return rows


def parse_brknews_title_rows(output: list[dict], symbol: str, fetched_at: str) -> list[dict]:
    rows = []
    for item in output:
        headline = (item.get("hts_pbnt_titl_cntt") or "").strip()
        news_key = item.get("cntt_usiq_srno")
        if not headline or not news_key:
            continue
        rows.append({
            "source_api": "brknews_title",
            "news_key": news_key,
            "symbol": symbol,
            "headline": headline,
            "published_at": _published_at(item.get("data_dt", ""), item.get("data_tm", "")),
            "source": item.get("dorg"),
            "fetched_at": fetched_at,
        })
    return rows


def collect_headlines(client, symbol: str, rate_limiter: RateLimiter) -> list[dict]:
    now = datetime.now(KST).isoformat()

    rate_limiter.wait()
    news_resp = client.news_title_overseas(symbol=symbol)
    news_rows = parse_news_title_rows(news_resp.get("outblock1", []), symbol, now)

    rate_limiter.wait()
    brknews_resp = client.brknews_title_overseas(symbol=symbol)
    brknews_rows = parse_brknews_title_rows(brknews_resp.get("output", []), symbol, now)

    return news_rows + brknews_rows


def tag_untagged(conn: sqlite3.Connection, config: AmznConfig, limit: int = 100) -> int:
    """Tag whatever's currently untagged, one LLM call per headline, and
    freeze each result immediately (not batched into one commit) so a
    mid-run failure doesn't lose already-tagged rows."""
    pending = storage.untagged_news_events(conn, limit=limit)
    config_hash = config.get_hash()
    n_tagged = 0
    for item in pending:
        tag = llm_tagger.tag_headline(item["headline"], config)
        if tag is None:
            continue  # stays NULL - honest failure, not a guessed default
        storage.save_news_tag(
            conn, item["source_api"], item["news_key"], tag, config_hash,
            datetime.now(KST).isoformat())
        conn.commit()
        n_tagged += 1
    return n_tagged


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Collect AMZN news headlines and tag events")
    parser.add_argument("--account", default="kis-main")
    parser.add_argument("--symbol", default="AMZN")
    parser.add_argument("--skip-tagging", action="store_true",
                         help="Collect headlines only; leave LLM tagging for a later run")
    args = parser.parse_args()

    storage.init_db()
    config = AmznConfig()
    client = accounts.get_client(args.account)
    rate_limiter = RateLimiter()

    rows = collect_headlines(client, args.symbol, rate_limiter)

    with sqlite3.connect(storage.DB_PATH) as conn:
        storage.save_news_events(conn, rows)
        conn.commit()
        log.info("collected %d headline(s) (dedup via INSERT OR IGNORE)", len(rows))

        if not args.skip_tagging:
            n_tagged = tag_untagged(conn, config)
            log.info("tagged %d headline(s)", n_tagged)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
