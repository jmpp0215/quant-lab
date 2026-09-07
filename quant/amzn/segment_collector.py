"""AMZN segment financials (AWS revenue/operating income, advertising
revenue, capex, OCF, FCF) from SEC EDGAR's raw XBRL instance documents.

Why raw XBRL and not data.sec.gov's companyconcept/companyfacts API:
confirmed by direct query (2026-09-07) that those endpoints aggregate away
the us-gaap:StatementBusinessSegmentsAxis / srt:ProductOrServiceAxis
dimensional facts this module needs - they only expose consolidated
(non-dimensional) totals. Each filing's own "{doc}_htm.xml" instance file
(SEC's raw XBRL underlying its inline-XBRL primary document) does carry
the dimensional contexts, confirmed against Amazon's 2026-07-31 10-Q
(accession 0001018724-26-000026): AWS revenue/operating income live under
contexts tagging dimension us-gaap:StatementBusinessSegmentsAxis, member
amzn:AmazonWebServicesSegmentMember; advertising revenue under
srt:ProductOrServiceAxis, member amzn:AdvertisingServicesMember.

Point-in-time: every fact is stored keyed by (period_end, metric,
accession_number) and never overwritten - see storage.py's schema comment
and quant/pead/dart.py's normalize_to_quarterly for the same principle.
Any code reading this table for a historical as_of_date must filter to
filing_date <= as_of_date and take the latest accession_number, exactly
like dart.py's _get_pit_value().

Collection only - no signal logic here.

CLI:
    python -m quant.amzn.segment_collector [--symbol AMZN] [--cik 0001018724]
"""
import argparse
import logging
import sqlite3
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import requests

from quant.amzn import storage
from quant.amzn.config import AmznConfig

KST = ZoneInfo("Asia/Seoul")
log = logging.getLogger("amzn.segment_collector")

SEC_BASE = "https://www.sec.gov/Archives/edgar/data"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# SEC requests a descriptive User-Agent identifying the requester -
# unauthenticated but enforced; a generic requests default gets 403'd.
SEC_USER_AGENT = "quant-lab research (contact: set SEC_EDGAR_USER_AGENT in .env)"

XBRLI_NS = "http://www.xbrl.org/2003/instance"
XBRLDI_NS = "http://xbrl.org/2006/xbrldi"
DEI_NS_PREFIX = "{http://xbrl.sec.gov/dei/"  # year suffix varies per filing

NS = {"xbrli": XBRLI_NS, "xbrldi": XBRLDI_NS}

# metric -> (concept local name, dimension attr on AmznConfig, member attr on
# AmznConfig) or None for the two dimension fields when the metric is
# consolidated (undimensioned).
METRIC_SPECS = {
    "aws_revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax",
                     "segment_axis", "aws_member"),
    "aws_operating_income": ("OperatingIncomeLoss", "segment_axis", "aws_member"),
    "advertising_revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax",
                             "advertising_axis", "advertising_member"),
    "capex": ("PaymentsToAcquireProductiveAssets", None, None),
    "ocf": ("NetCashProvidedByUsedInOperatingActivities", None, None),
}

FORM_DURATION_DAYS = {
    "10-Q": (75, 100),   # one quarter
    "10-K": (350, 380),  # one fiscal year
}


def _headers() -> dict:
    import os
    return {"User-Agent": os.getenv("SEC_EDGAR_USER_AGENT", SEC_USER_AGENT)}


def fetch_submissions(cik: str) -> dict:
    resp = requests.get(SEC_SUBMISSIONS_URL.format(cik=cik.zfill(10)),
                         headers=_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json()


def list_filings(submissions: dict, forms: tuple = ("10-Q", "10-K")) -> list[dict]:
    recent = submissions["filings"]["recent"]
    out = []
    for i, form in enumerate(recent["form"]):
        if form in forms:
            out.append({
                "form": form,
                "filing_date": recent["filingDate"][i],
                "accession_number": recent["accessionNumber"][i],
                "primary_document": recent["primaryDocument"][i],
            })
    return out


def instance_xml_url(cik: str, accession_number: str, primary_document: str) -> str:
    """SEC's convention for inline-XBRL filings: the raw instance document
    sits alongside the primary .htm as "{basename}_htm.xml" - confirmed
    against Amazon's own filing directory listing (2026-07-31 10-Q)."""
    accession_nodashes = accession_number.replace("-", "")
    basename = primary_document.removesuffix(".htm")
    return f"{SEC_BASE}/{int(cik)}/{accession_nodashes}/{basename}_htm.xml"


def fetch_instance_xml(cik: str, accession_number: str, primary_document: str) -> str:
    url = instance_xml_url(cik, accession_number, primary_document)
    resp = requests.get(url, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.text


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def parse_document_period_end(root: ET.Element) -> date | None:
    for elem in root.iter():
        if _local(elem.tag) == "DocumentPeriodEndDate" and elem.text:
            return date.fromisoformat(elem.text.strip())
    return None


def _contexts_by_dimension(root: ET.Element, dimension: str | None,
                            member: str | None) -> dict[str, tuple[date, date]]:
    """context id -> (start, end) for every context whose *only* dimensional
    member (if any) matches (dimension, member). dimension=None means "no
    segment dimension at all" (consolidated figures)."""
    out = {}
    for ctx in root.findall("xbrli:context", NS):
        cid = ctx.get("id")
        members = ctx.findall(".//xbrldi:explicitMember", NS)
        period = ctx.find("xbrli:period", NS)
        start_el = period.find("xbrli:startDate", NS) if period is not None else None
        end_el = period.find("xbrli:endDate", NS) if period is not None else None
        if start_el is None or end_el is None:
            continue  # instant (balance-sheet) context, not a duration - not what we need here

        if dimension is None:
            if members:
                continue  # dimensioned context, but we want the consolidated one
        else:
            match = any(m.get("dimension") == dimension and (m.text or "").strip() == member
                        for m in members)
            if not match:
                continue

        out[cid] = (date.fromisoformat(start_el.text), date.fromisoformat(end_el.text))
    return out


def _select_context(contexts: dict[str, tuple[date, date]], doc_period_end: date,
                     form_type: str) -> str | None:
    """Among candidate contexts, pick the one whose end matches the filing's
    own document period end and whose duration matches this form type's
    expected reporting period (quarter for 10-Q, year for 10-K) - filters
    out the prior-year comparative and YTD-cumulative contexts that sit
    alongside it in the same filing."""
    lo, hi = FORM_DURATION_DAYS.get(form_type, (0, 10_000))
    for cid, (start, end) in contexts.items():
        if end != doc_period_end:
            continue
        if lo <= (end - start).days <= hi:
            return cid
    return None


def _index_facts_by_concept(root: ET.Element) -> dict[str, list[ET.Element]]:
    """concept local name -> every tagged fact element with that name,
    built with one pass over the document. find_metric_value() would
    otherwise re-scan the whole (multi-MB) instance with root.iter() once
    per metric - 5+ full walks per filing for no reason."""
    index: dict[str, list[ET.Element]] = {}
    for elem in root.iter():
        if elem.get("contextRef") is not None:
            index.setdefault(_local(elem.tag), []).append(elem)
    return index


def find_metric_value(fact_index: dict[str, list[ET.Element]], root: ET.Element,
                       concept: str, dimension: str | None, member: str | None,
                       doc_period_end: date, form_type: str
                       ) -> tuple[float | None, bool, date | None]:
    """Returns (value, is_estimable, period_start). is_estimable=False
    (value=None, period_start=None) if the expected context or fact tag
    isn't present in this filing - never a silent 0 or a fabricated period,
    per the project's honest-failure convention."""
    contexts = _contexts_by_dimension(root, dimension, member)
    ctx_id = _select_context(contexts, doc_period_end, form_type)
    if ctx_id is None:
        return None, False, None

    for elem in fact_index.get(concept, []):
        if elem.get("contextRef") == ctx_id:
            try:
                return float(Decimal(elem.text.strip())), True, contexts[ctx_id][0]
            except (TypeError, ValueError, ArithmeticError):
                return None, False, None
    return None, False, None


def parse_filing(xml_text: str, form_type: str, filing_date: str,
                  accession_number: str, config: AmznConfig,
                  symbol: str = "AMZN") -> list[dict]:
    root = ET.fromstring(xml_text)
    doc_period_end = parse_document_period_end(root)
    if doc_period_end is None:
        log.error("%s: no DocumentPeriodEndDate found, skipping", accession_number)
        return []

    fact_index = _index_facts_by_concept(root)

    values = {}
    for metric, (concept, dim_attr, member_attr) in METRIC_SPECS.items():
        dimension = getattr(config, dim_attr) if dim_attr else None
        member = getattr(config, member_attr) if member_attr else None
        value, is_estimable, period_start = find_metric_value(
            fact_index, root, concept, dimension, member, doc_period_end, form_type)
        values[metric] = (value, is_estimable, period_start)

    # fcf is derived, not tagged - only estimable if both inputs are, and
    # takes ocf's own resolved period_start rather than a separate lookup
    # (both are consolidated/undimensioned, so they share one context).
    ocf_val, ocf_ok, ocf_start = values["ocf"]
    capex_val, capex_ok, _ = values["capex"]
    if ocf_ok and capex_ok:
        values["fcf"] = (ocf_val - capex_val, True, ocf_start)
    else:
        values["fcf"] = (None, False, None)

    rows = []
    for metric, (value, is_estimable, period_start) in values.items():
        rows.append({
            "symbol": symbol,
            "period_start": period_start.isoformat() if period_start else None,
            "period_end": doc_period_end.isoformat(),
            "metric": metric,
            "value": value,
            "is_estimable": int(is_estimable),
            "filing_date": filing_date,
            "accession_number": accession_number,
            "form_type": form_type,
            "config_hash": config.get_hash(),
        })
    return rows


def collect_new_filings(conn: sqlite3.Connection, cik: str, symbol: str,
                         config: AmznConfig, max_filings: int = 8) -> int:
    """Fetch and parse filings not already in amzn_segment_fetch_state.
    Returns the count of newly-parsed filings. One filing's fetch/parse
    failure is logged and skipped, not fatal to the rest (same pattern as
    dividends.sync_all)."""
    submissions = fetch_submissions(cik)
    filings = list_filings(submissions)[:max_filings]

    n_new = 0
    for filing in filings:
        if storage.is_accession_fetched(conn, filing["accession_number"]):
            continue
        try:
            xml_text = fetch_instance_xml(
                cik, filing["accession_number"], filing["primary_document"])
            rows = parse_filing(
                xml_text, filing["form"], filing["filing_date"],
                filing["accession_number"], config, symbol)
        except Exception as e:
            log.error("%s (%s): fetch/parse failed: %s",
                      filing["accession_number"], filing["form"], e)
            continue

        storage.save_segment_facts(conn, rows)
        storage.save_segment_fetch_state(
            conn, filing["accession_number"], symbol, filing["filing_date"],
            datetime.now(KST).isoformat())
        conn.commit()
        n_new += 1
        log.info("%s (%s, period end %s): %d facts saved",
                  filing["accession_number"], filing["form"],
                  rows[0]["period_end"] if rows else "?", len(rows))

    return n_new


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Collect AMZN segment financials from SEC EDGAR XBRL")
    parser.add_argument("--symbol", default="AMZN")
    parser.add_argument("--cik", default="0001018724", help="Amazon.com, Inc.'s SEC CIK")
    parser.add_argument("--max-filings", type=int, default=8)
    args = parser.parse_args()

    storage.init_db()
    config = AmznConfig()

    with sqlite3.connect(storage.DB_PATH) as conn:
        n_new = collect_new_filings(conn, args.cik, args.symbol, config, args.max_filings)

    log.info("done: %d new filing(s) parsed", n_new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
