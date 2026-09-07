# AMZN data collection layer

Pure data collection for AMZN. No signal generation, no order execution -
those are deliberately out of scope for this stage (see "Not yet built"
below). Follows the project's layer separation: collection here, signal
generation and execution live elsewhere and are not touched by this
package. `quant/rebalance.py`, `quant/executor.py`, and the main
`quant/momentum.py`/`quant/strategy.py`/`quant/allocation.py` strategy are
unmodified by this work.

## Modules

| Module | Source | What it collects |
|---|---|---|
| `price_collector.py` | KIS 해외주식 현재가상세 (`HHDFS76200200`) | Daily price + valuation ratios (PER/PBR/EPS/BPS/market cap/shares/volume) |
| `segment_collector.py` | SEC EDGAR, raw XBRL instance per filing | AWS revenue/operating income, advertising revenue, capex, OCF, FCF |
| `news_collector.py` | KIS 해외뉴스종합(제목) `HHPSTH60100C1` + 해외속보(제목) `FHKST01011801` | Headlines, LLM-tagged into event categories |

## Auth requirements

**KIS Open API (price_collector.py, news_collector.py)**
- Needs a **live (실전) KIS account** with valid `KIS_<ACCOUNT>_APP_KEY` /
  `_APP_SECRET` / `_CANO` / `_ACNT_PRDT_CD` in `.env` (see `quant/accounts.py`).
  Overseas quote endpoints (price-detail, news-title, brknews-title) are
  **not available on a paper-trading (모의투자) account** - confirmed by KIS's
  own documentation and consistent with this project's existing
  `orderbook_overseas`/`holdings_overseas` usage.
- Defaults to the `kis-main` account (read-only market-data queries, not the
  `kis-isa` strategy account) - override with `--account`.
- Rate limit: KIS caps overseas quote calls at 20/sec. `rate_limit.py`
  throttles to 15/sec to leave margin.

**SEC EDGAR (segment_collector.py)**
- No authentication, but SEC requires a descriptive `User-Agent` header on
  every request or it 403s. Set `SEC_EDGAR_USER_AGENT` in `.env` (e.g.
  `"Your Name your-email@example.com"`) - falls back to a generic default
  that identifies this as research traffic, but SEC prefers a real contact.
- No documented rate limit beyond "be reasonable"; this collector makes at
  most `--max-filings` (default 8) requests per run, once per new filing
  ever (state-tracked in `amzn_segment_fetch_state`), so it's inherently
  low-volume.

**LLM tagging (news_collector.py's tagging step, via `llm_tagger.py`)**
- Needs `anthropic` (in `requirements.txt`, only `llm_tagger.py` imports it -
  the import is deferred to call time so nothing else in this package
  requires it installed) and `ANTHROPIC_API_KEY` in `.env`.
- Run with `--skip-tagging` to collect headlines without needing either.

## Why SEC EDGAR segment data uses raw XBRL, not the JSON API

`data.sec.gov/api/xbrl/companyconcept` and `companyfacts` were the obvious
first choice, but a direct query against them (2026-09-07) confirmed they
**aggregate away segment/product dimensional facts** - they only expose
consolidated (non-dimensional) totals. AWS revenue, AWS operating income,
and advertising revenue only exist in each filing's own segment-reporting
note, which is only present in the filing's raw XBRL instance document
(`{accession}/{primary-doc-basename}_htm.xml`), tagged with
`us-gaap:StatementBusinessSegmentsAxis` (member
`amzn:AmazonWebServicesSegmentMember` for AWS) and
`srt:ProductOrServiceAxis` (member `amzn:AdvertisingServicesMember` for
advertising). `segment_collector.py` parses that document directly with
Python's stdlib `xml.etree.ElementTree` (no new dependency).

Each 10-Q instance document contains the current quarter, the prior-year
quarter, the current fiscal-year-to-date, and the prior-year-to-date as
separate `<context>` blocks for the same tagged concept.
`_select_context()` picks the one whose period end matches the filing's own
`dei:DocumentPeriodEndDate` and whose duration matches the form type
(~1 quarter for 10-Q, ~1 year for 10-K) - confirmed correct against
Amazon's real 2026-07-31 10-Q (`quant/amzn/tests/test_segment_collector.py`
also exercises this against a synthetic instance with the same ambiguity).

## Point-in-time principle

Same as `quant/pead/dart.py`: raw facts are never overwritten. Segment
facts are keyed by `(symbol, period_end, metric, accession_number)`, so a
later filing that restates a prior period lands as a new row under its own
accession number rather than replacing the original. Any future signal
code reading this table for a historical `as_of_date` must filter to
`filing_date <= as_of_date` and take the latest `accession_number` for that
period - exactly like `dart.py`'s `normalize_to_quarterly`/`_get_pit_value`.

## Honest-failure principle

Every collector returns `None` (`is_estimable=0` where that column exists)
rather than `0` or a guessed value when a field is missing, unparseable, or
a filing simply doesn't tag something. See each module's parsing functions
(`parse_price_detail`, `find_metric_value`, `llm_tagger.tag_headline`) and
their tests.

## Config reproducibility

`config.py`'s `AmznConfig.get_hash()` (same pattern as
`quant/pead/config.py`'s `PeadConfig`) is stored alongside every row so a
later change to which XBRL dimension/member is treated as "AWS", which LLM
model/prompt tags headlines, etc. doesn't silently blend with rows produced
under a different assumption.

## Running the collectors

```bash
source .venv/bin/activate
python -m quant.amzn.price_collector [--account kis-main] [--symbol AMZN]
python -m quant.amzn.segment_collector [--cik 0001018724] [--max-filings 8]
python -m quant.amzn.news_collector [--account kis-main] [--skip-tagging]
```

Each is safe to re-run: price/news collection is idempotent per day
(`amzn_price_fetch_state`) or per headline (`INSERT OR IGNORE` keyed by
`news_key`), and segment collection skips accession numbers already in
`amzn_segment_fetch_state`.

## Not yet built (deliberately out of scope this stage)

- Signal generation (valuation-overlay percentile calculation, conviction
  score, etc.)
- Trade execution / tranche sizing
- `DRY_RUN` gate - N/A, there is no order logic yet
- `launchd` automation - will be added once a few days/weeks of data have
  accumulated
