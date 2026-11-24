"""Rights-offering hold ban for the PBR Top 50 (pure).

User rule, 2026-11-24: once a company's own 유상증자 decision is filed, the
symbol may be neither bought nor held, whatever the allotment method or
size. It comes back as a candidate on the day a filing whose balance sheet
already contains the new equity and the new shares is received - the first
report with both total_equity and issued_shares (what prepare_pbr_signals
needs for BPS) whose period ends after the payment date. A withdrawal lifts
the ban from the withdrawal filing's date. Applied at each fresh PBR
ranking: a banned symbol is left out before the Top 50 cut, so a held one
is sold by the normal diff.

Why: until then BPS mixes pre-offering equity and share count with a price
that already carries the dilution. SK디앤디 (210980) was bought on
2026-09-14 at a computed PBR of ~0.15 against ~0.40 with the 2.4x share
issue (decided 2026-07-28, paid 2026-10-15) counted in.

The network side (DART lists, filing bodies) lives in
scripts/rights_offerings.py.
"""

import re
from dataclasses import dataclass

from quant.stock_factors.disclosures import base_title

DECISION_TITLE = "주요사항보고서(유상증자결정)"

# report_code -> fiscal period end (MMDD) of target_year.
PERIOD_END = {"11013": "0331", "11012": "0630", "11014": "0930", "11011": "1231"}

# The numbered form field, not the prose mentions ("주금납입일에 ..."): the
# shareholder-allotment form writes "12. 납입일 2026년 10월 15일", the
# third-party form "9. 납입일 2026.04.17".
_PAYMENT_RE = re.compile(
    r"\d{1,2}\s*\.\s*납입일\s*(\d{4})\s*(?:년|[.-])\s*(\d{1,2})\s*(?:월|[.-])\s*(\d{1,2})")


@dataclass(frozen=True)
class Offering:
    symbol: str
    announced: str            # YYYYMMDD, receipt date of the decision filing
    versions: tuple[str, ...]  # rcept_nos of the decision and its corrections, newest first
    withdrawn: str | None = None

    @property
    def latest_rcept_no(self) -> str:
        return self.versions[0]


def is_decision(report_nm: str) -> bool:
    """The decision itself or a correction of it ([기재정정] etc.)."""
    return base_title(report_nm) == DECISION_TITLE


def is_withdrawal(report_nm: str) -> bool:
    title = " ".join(report_nm.split())
    return "철회" in title and "유상증자" in title


# Where the decision form itself starts; a correction's 정정사항 table comes
# before it.
_FORM_HEADER = "거래소 신고의무"
_HEAD_CHARS = 3000
# "유상증자 철회", "유상증자 결정 철회", "유상증자를 철회", "발행 결정 철회" - not a
# bare 철회: 다이나믹디자인's 2026-05-06 payment delay says "투자 의사 철회에
# 따른 사항은 아니며".
_WITHDRAWN_RE = re.compile(r"유상증자\s*(?:결정\s*)?를?\s*철회|발행\s*결정\s*철회")


def correction_head(body: str) -> str:
    """The text before the decision form: a correction's 정정사항, or ""."""
    return body.split(_FORM_HEADER, 1)[0][:_HEAD_CHARS] if _FORM_HEADER in body else ""


def says_withdrawn(head: str) -> bool:
    """Whether a decision filing's 정정사항 (correction_head) says the
    offering is withdrawn.

    DART has no separate withdrawal title: 롯데렌탈 (089860) withdrew its
    2025-02-28 offering with a [기재정정] of the decision whose table reads
    "유상증자 결정 -> 유상증자 철회" (2026-05-21). Only the text before the
    form is read, so a 철회 in the form's own terms doesn't count.
    """
    return bool(_WITHDRAWN_RE.search(head))


def group_offerings(filings: list[dict]) -> list[Offering]:
    """Offerings from DART list rows ({stock_code, rcept_dt, rcept_no,
    report_nm}) of one market-wide scan.

    An original decision starts an offering; later corrections are added to
    its versions and a withdrawal title closes it (a withdrawal filed as a
    correction is read from its body, says_withdrawn). A correction with no
    original in the scan (the original predates the lookback) starts one at
    its own date - later than the real start, never missing the ban.
    """
    offerings: dict[str, list[Offering]] = {}
    for f in sorted(filings, key=lambda f: (f["rcept_dt"], f["rcept_no"])):
        sym, nm = f.get("stock_code", "").strip(), f["report_nm"]
        if not sym:
            continue
        events = offerings.setdefault(sym, [])
        last = events[-1] if events else None
        is_open = last is not None and last.withdrawn is None
        if is_decision(nm):
            is_original = not nm.lstrip().startswith("[")
            if is_original or not is_open:
                events.append(Offering(sym, f["rcept_dt"], (f["rcept_no"],)))
            else:
                events[-1] = Offering(sym, last.announced, (f["rcept_no"], *last.versions))
        elif is_withdrawal(nm) and is_open:
            events[-1] = Offering(sym, last.announced, last.versions, f["rcept_dt"])
    return [o for events in offerings.values() for o in events]


def parse_payment_date(body: str) -> str | None:
    """YYYYMMDD of the 납입일 field in a decision filing's body, or None.

    The last match: a correction opens with a 정정전/정정후 table that can
    carry the old date, and the corrected form follows it.
    """
    matches = _PAYMENT_RE.findall(body)
    if not matches:
        return None
    y, mo, d = matches[-1]
    return f"{y}{int(mo):02d}{int(d):02d}"


def release_date(payment: str, reports: list[tuple[str, str]]) -> str | None:
    """Receipt date of the first report (rcept_dt, period_end) whose period
    ends after `payment`, or None while there is none.

    Strictly after: new shares take effect the day after payment, so a
    period ending on the payment date can hold the new equity with the old
    share count.
    """
    dates = [rcept for rcept, end in reports if end > payment]
    return min(dates) if dates else None


def banned(offerings: list[Offering], payments: dict[str, str | None],
           reports_by_symbol: dict[str, list[tuple[str, str]]],
           as_of: str) -> dict[str, str]:
    """{symbol: reason} for the symbols under the ban on `as_of` (YYYYMMDD).

    payments: latest_rcept_no -> payment date (None when the body had no
    readable 납입일 - the ban then stays until a person looks, the same
    safe default as value.compute_quarantine_windows).
    """
    out: dict[str, str] = {}
    for o in offerings:
        if o.announced > as_of:
            continue
        if o.withdrawn is not None and o.withdrawn <= as_of:
            continue
        payment = payments.get(o.latest_rcept_no)
        if payment is None:
            out[o.symbol] = f"유상증자 결정 {o.announced}, 납입일 미확인"
            continue
        release = release_date(payment, reports_by_symbol.get(o.symbol, []))
        if release is None or release > as_of:
            out[o.symbol] = f"유상증자 결정 {o.announced}, 납입 {payment}, 반영 보고서 대기"
    return out
