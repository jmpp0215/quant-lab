"""SQLite schema for the AMZN data-collection layer, in quant.db.

Collection only - no signal generation reads these tables yet (see
quant/amzn/README.md). Mirrors quant/pead/storage.py's shape: a schema
string executed via executescript(), plus small save/query helpers per
table.
"""
import sqlite3
from pathlib import Path

AMZN_SCHEMA = """
-- KIS 해외주식 현재가상세 (HHDFS76200200) 일별 스냅샷. Config-agnostic: raw
-- fields as returned, no derived/interpreted columns. config_hash tracks
-- which AmznConfig produced this row (endpoint/exchange choice), not any
-- signal-layer interpretation - none of this table's values change with
-- config, but the column is kept for traceability per the project's
-- reproducibility convention (see pead_surprises for the same pattern
-- applied where config does matter).
CREATE TABLE IF NOT EXISTS amzn_price_daily (
    date TEXT NOT NULL,              -- YYYYMMDD, KST fetch date
    symbol TEXT NOT NULL DEFAULT 'AMZN',
    last_price REAL,                 -- last (현재가)
    per REAL,                        -- perx
    pbr REAL,                        -- pbrx
    eps REAL,                        -- epsx
    bps REAL,                        -- bpsx
    market_cap REAL,                 -- tomv (시가총액)
    shares_outstanding REAL,         -- shar (상장주식수)
    volume REAL,                     -- tvol (거래량)
    currency TEXT,
    config_hash TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (date, symbol, config_hash)
);

-- Per-symbol incremental-fetch state, same shape as pead_price_fetch_state /
-- quant/storage.py's dividend_fetch_state - "have we already collected
-- today" gate, not used for backfill windowing since KIS only exposes the
-- current snapshot (no historical price-detail range query).
CREATE TABLE IF NOT EXISTS amzn_price_fetch_state (
    symbol      TEXT PRIMARY KEY,
    fetched_to  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- SEC EDGAR 10-Q/10-K segment data, parsed from each filing's raw XBRL
-- instance document (not data.sec.gov's companyconcept/companyfacts API -
-- confirmed by direct query that those aggregate away the segment/product
-- dimension members this table needs).
--
-- Point-in-time: keyed by (symbol, period_end, metric, accession_number),
-- never overwritten. A later filing that restates a prior period's segment
-- figure (e.g. the following 10-K's comparative column) lands as a new row
-- under its own accession_number rather than replacing the original - the
-- same non-destructive-correction principle as pead_dart_raw. Point-in-time
-- readers must pick the latest accession_number with filing_date <=
-- as_of_date, exactly like dart.py's normalize_to_quarterly()/_get_pit_value.
--
-- metric: aws_revenue, aws_operating_income, advertising_revenue, capex,
-- ocf, fcf (fcf = ocf - capex, computed at collection time from the same
-- filing's own ocf/capex - not a raw XBRL tag).
-- is_estimable=0 means the expected XBRL context/tag wasn't found in this
-- filing (e.g. a taxonomy rename) - value is NULL, never a silent 0.
CREATE TABLE IF NOT EXISTS amzn_segment_quarterly (
    symbol TEXT NOT NULL DEFAULT 'AMZN',
    period_start TEXT,     -- NULL when is_estimable=0 (context couldn't be resolved)
    period_end TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL,
    is_estimable INTEGER NOT NULL DEFAULT 1,
    filing_date TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    form_type TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, period_end, metric, accession_number)
);

-- Which SEC filings (accession numbers) have already been parsed, so
-- reruns skip them - same fetch-state idiom as the price/dividend tables.
CREATE TABLE IF NOT EXISTS amzn_segment_fetch_state (
    accession_number TEXT PRIMARY KEY,
    symbol           TEXT NOT NULL,
    filing_date      TEXT NOT NULL,
    fetched_at       TEXT NOT NULL
);

-- KIS 해외뉴스종합(제목)/해외속보(제목) headlines, LLM-tagged once at
-- collection time and frozen - event_tag/llm_config_hash/tagged_at are
-- never recomputed by a later read. NULL event_tag means "not yet tagged
-- (or tagging failed)", never a guessed default category.
CREATE TABLE IF NOT EXISTS amzn_news_events (
    source_api TEXT NOT NULL,        -- 'news_title' | 'brknews_title'
    news_key TEXT NOT NULL,          -- news_key (news_title) or cntt_usiq_srno (brknews_title)
    symbol TEXT NOT NULL DEFAULT 'AMZN',
    headline TEXT NOT NULL,
    published_at TEXT NOT NULL,      -- ISO-ish, from data_dt+data_tm
    source TEXT,                     -- 자료원/뉴스제공업체
    event_tag TEXT,                  -- LLM-assigned, frozen; NULL until tagged
    llm_config_hash TEXT,            -- AmznConfig hash active when tagged
    tagged_at TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source_api, news_key)
);
"""

DB_PATH = Path(__file__).parent.parent.parent / "data" / "quant.db"


def init_db(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(AMZN_SCHEMA)


def price_fetch_state(conn: sqlite3.Connection, symbol: str) -> str | None:
    row = conn.execute(
        "SELECT fetched_to FROM amzn_price_fetch_state WHERE symbol = ?",
        (symbol,),
    ).fetchone()
    return row[0] if row else None


def save_price_fetch_state(conn: sqlite3.Connection, symbol: str,
                            fetched_to: str, updated_at: str) -> None:
    conn.execute(
        "INSERT INTO amzn_price_fetch_state (symbol, fetched_to, updated_at) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(symbol) DO UPDATE SET fetched_to=excluded.fetched_to, "
        "updated_at=excluded.updated_at",
        (symbol, fetched_to, updated_at),
    )


def save_price_daily(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO amzn_price_daily "
        "(date, symbol, last_price, per, pbr, eps, bps, market_cap, "
        " shares_outstanding, volume, currency, config_hash, fetched_at) "
        "VALUES (:date, :symbol, :last_price, :per, :pbr, :eps, :bps, "
        " :market_cap, :shares_outstanding, :volume, :currency, "
        " :config_hash, :fetched_at)",
        row,
    )


def is_accession_fetched(conn: sqlite3.Connection, accession_number: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM amzn_segment_fetch_state WHERE accession_number = ?",
        (accession_number,),
    ).fetchone()
    return row is not None


def save_segment_fetch_state(conn: sqlite3.Connection, accession_number: str,
                              symbol: str, filing_date: str,
                              fetched_at: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO amzn_segment_fetch_state "
        "(accession_number, symbol, filing_date, fetched_at) VALUES (?, ?, ?, ?)",
        (accession_number, symbol, filing_date, fetched_at),
    )


def save_segment_facts(conn: sqlite3.Connection, facts: list[dict]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO amzn_segment_quarterly "
        "(symbol, period_start, period_end, metric, value, is_estimable, "
        " filing_date, accession_number, form_type, config_hash) "
        "VALUES (:symbol, :period_start, :period_end, :metric, :value, "
        " :is_estimable, :filing_date, :accession_number, :form_type, "
        " :config_hash)",
        facts,
    )


def save_news_events(conn: sqlite3.Connection, events: list[dict]) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO amzn_news_events "
        "(source_api, news_key, symbol, headline, published_at, source, "
        " fetched_at) "
        "VALUES (:source_api, :news_key, :symbol, :headline, :published_at, "
        " :source, :fetched_at)",
        events,
    )


def untagged_news_events(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT source_api, news_key, headline FROM amzn_news_events "
        "WHERE event_tag IS NULL LIMIT ?",
        (limit,),
    ).fetchall()
    return [{"source_api": r[0], "news_key": r[1], "headline": r[2]} for r in rows]


def save_news_tag(conn: sqlite3.Connection, source_api: str, news_key: str,
                   event_tag: str, llm_config_hash: str, tagged_at: str) -> None:
    conn.execute(
        "UPDATE amzn_news_events SET event_tag = ?, llm_config_hash = ?, "
        "tagged_at = ? WHERE source_api = ? AND news_key = ?",
        (event_tag, llm_config_hash, tagged_at, source_api, news_key),
    )
