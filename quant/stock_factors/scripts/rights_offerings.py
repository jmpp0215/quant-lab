"""DART side of the rights-offering hold ban (quant/stock_factors/rights_offering.py).

run_pbr_live calls banned_symbols() before each fresh ranking. Any DART
failure raises: the run then stops rather than trade without the ban.

    python quant/stock_factors/scripts/rights_offerings.py [--date 2026-09-14]
        print the symbols banned on that date (default today); no orders
"""

import argparse
import io
import json
import logging
import sys
import zipfile
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import requests  # noqa: E402

from quant import market  # noqa: E402
from quant.pead import dart  # noqa: E402
from quant.stock_factors import disclosures, rights_offering, value  # noqa: E402

log = logging.getLogger("rights_offerings")

# Decision -> payment is at most a few months and payment -> the first report
# whose period ends after it at most ~9 months (paid in early July, next
# balance sheet with share count is often the annual report in March).
LOOKBACK_DAYS = 540
# DART caps a list.json search without corp_code at three months.
WINDOW_DAYS = 90
FILING_CACHE = dart.DART_CACHE_DIR / "rights_filings.json"


def scan_windows(as_of: date, lookback: int = LOOKBACK_DAYS,
                 step: int = WINDOW_DAYS) -> list[tuple[str, str]]:
    """(bgn_de, end_de) pairs covering [as_of - lookback, as_of], each at
    most `step` days, oldest first."""
    out = []
    start = as_of - timedelta(days=lookback)
    while start <= as_of:
        end = min(start + timedelta(days=step - 1), as_of)
        out.append((start.strftime("%Y%m%d"), end.strftime("%Y%m%d")))
        start = end + timedelta(days=1)
    return out


def list_market_filings(bgn_de: str, end_de: str) -> list[dict]:
    """KOSPI 주요사항보고 (pblntf_ty B) filings received in the window."""
    out, page = [], 1
    while True:
        r = dart._rate_limit_and_retry(f"{dart.OPENDART_BASE_URL}/list.json", {
            "crtfc_key": dart._get_api_key(), "bgn_de": bgn_de, "end_de": end_de,
            "pblntf_ty": "B", "corp_cls": "Y", "page_no": page, "page_count": 100})
        # 013 (no filings) comes back as {"list": []} without a status key
        if r.get("status") == "013" or ("status" not in r and r.get("list") == []):
            return out
        if r.get("status") != "000":
            raise RuntimeError(f"DART list.json status {r.get('status')}: {r.get('message')}")
        out += r.get("list", [])
        if page >= int(r.get("total_page", 1)):
            return out
        page += 1


def full_body(rcept_no: str) -> str:
    res = requests.get(f"{dart.OPENDART_BASE_URL}/document.xml", params={
        "crtfc_key": dart._get_api_key(), "rcept_no": rcept_no}, timeout=60)
    res.raise_for_status()
    try:
        with zipfile.ZipFile(io.BytesIO(res.content)) as z:
            raw = z.read(z.namelist()[0]).decode("utf-8", "ignore")
    except zipfile.BadZipFile:
        log.warning("%s: no DART document", rcept_no)
        return ""
    return disclosures.body_text(raw, limit=10**7)


def filing_facts(offerings: list[rights_offering.Offering]
                 ) -> tuple[dict[str, str | None], dict[str, str]]:
    """(payments, withdrawals) keyed by each offering's latest_rcept_no.

    payments: 납입일 from the newest version that states one (a correction
    can leave the field blank, or carry no document). withdrawals: the
    receipt date of the latest version when its 정정사항 says 철회. A filing
    never changes once received, so what was read is cached on disk.
    """
    cache = json.loads(FILING_CACHE.read_text()) if FILING_CACHE.exists() else {}

    def facts(no: str) -> dict:
        if no not in cache:
            body = full_body(no)
            # the 정정사항 text, not a verdict, so a changed rule applies to cached filings
            cache[no] = {"payment": rights_offering.parse_payment_date(body),
                         "head": rights_offering.correction_head(body)}
        return cache[no]

    payments: dict[str, str | None] = {}
    withdrawals: dict[str, str] = {}
    for o in offerings:
        if rights_offering.says_withdrawn(facts(o.latest_rcept_no)["head"]):
            withdrawals[o.latest_rcept_no] = o.latest_rcept_no[:8]  # rcept_no starts YYYYMMDD
            continue
        payment = next((facts(no)["payment"] for no in o.versions
                        if facts(no)["payment"]), None)
        if payment is None:
            log.warning("%s: no 납입일 in rights-offering filings %s - banned until checked",
                        o.symbol, ", ".join(o.versions))
        payments[o.latest_rcept_no] = payment
    FILING_CACHE.parent.mkdir(parents=True, exist_ok=True)
    FILING_CACHE.write_text(json.dumps(cache, indent=0, sort_keys=True))
    return payments, withdrawals


def banned_symbols(as_of: date) -> dict[str, str]:
    """{symbol: reason} under the hold ban on `as_of`."""
    filings = []
    for bgn, end in scan_windows(as_of):
        filings += list_market_filings(bgn, end)
    as_of_s = as_of.strftime("%Y%m%d")
    offerings = [o for o in rights_offering.group_offerings(filings)
                 if not (o.withdrawn and o.withdrawn <= as_of_s)]
    payments, withdrawals = filing_facts(offerings)
    offerings = [replace(o, withdrawn=withdrawals[o.latest_rcept_no])
                 if o.latest_rcept_no in withdrawals else o for o in offerings]
    return rights_offering.banned(offerings, payments, value.load_bps_reports(), as_of_s)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--date", type=date.fromisoformat, default=datetime.now(market.KST).date())
    args = p.parse_args()
    for sym, reason in sorted(banned_symbols(args.date).items()):
        print(sym, reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
