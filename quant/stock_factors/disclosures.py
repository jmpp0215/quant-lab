"""Disclosure bad-news flags for the PBR Top 50 (pure).

Record-only experiment, pre-registered in the AI study doc (candidate A,
2026-10-15): at each PBR rebalance, read the target symbols' DART filings
from the 60 days before the rebalance date and flag serious bad news. The
flags are never traded on - they are recorded so that, after ~12
rebalances, three flagging rules can be compared on the flagged symbols'
next-20-day excess return:

  R1  any filing whose title alone is severe
  R2  any severe or review-class filing (title only, broad)
  L   R1, or the LLM judged a review-class filing severe from its body

This module holds the title rules, the prompt and the reply parsing; the
network calls (DART, the LLM CLI) live in scripts/flag_disclosures.py.
"""

import json
import re
from datetime import date, timedelta

WINDOW_DAYS = 60

# Title-only: the filing type itself is the bad news.
SEVERE_KEYWORDS = (
    "횡령", "배임", "상장폐지", "상장적격성", "관리종목", "불성실공시",
    "회생절차", "파산", "부도", "영업정지", "감자결정", "투자주의환기",
    "매매거래정지", "해산",
)

# The type can be bad or harmless; only the body tells (size, terms).
REVIEW_KEYWORDS = (
    "유상증자", "전환사채", "신주인수권부사채", "교환사채", "감사보고서",
    "소송", "최대주주변경", "최대주주 변경", "채무보증", "담보제공",
    "자기주식처분", "투자유의", "벌금", "풍문", "투자판단관련주요경영사항",
    "손익구조", "영업실적", "양수", "양도", "합병", "분할",
    "단기차입금증가", "공급계약해지",
)

# Fixed list the LLM must choose from (pre-registered criteria 1-6).
CATEGORIES = (
    "감사의견·계속기업", "횡령·배임·상장폐지", "대규모 희석",
    "대규모 손실·소송·보증", "회생·영업중단", "최대주주 변경·매각", "해당없음",
)

SYSTEM_PROMPT = (
    "You review Korean DART corporate disclosures for a long-only value "
    "portfolio. Reply with a JSON array only, no prose."
)

PROMPT_TEMPLATE = """다음은 {name}({symbol})의 최근 공시들이다. 각 공시가 아래 기준 중 하나에 해당하는 "중대 악재"인지 판정하라.

중대 악재 기준:
1. 감사의견 비적정(한정·부적정·의견거절) 또는 계속기업 불확실성
2. 횡령·배임, 상장폐지·관리종목·거래정지 사유
3. 기존 발행주식 대비 10% 이상 희석되는 유상증자(제3자배정·일반공모)·CB·BW·EB 발행 (주주배정 유상증자와 무상증자는 해당하지 않는다)
4. 건별로 자기자본 대비 10% 이상인 소송·채무보증·담보제공, 대규모 손실·자본잠식 (기존 보증 누적 잔액만으로는 해당하지 않는다)
5. 회생·파산·영업정지·주요 사업 중단
6. 최대주주 변경 또는 최대주주의 대량 지분 매각

해당하지 않는 것: 일상적 보고, 호재, 소규모 거래, 이미 공시된 결정의 결과 보고.
"(자회사의 주요경영사항)" 공시는 이 회사 주주 입장에서 판단한다. 자회사 주식의 희석은 이 회사 주식의 희석이 아니다.
정정공시([기재정정] 등)는 정정으로 새로 생긴 악재만 판단한다. 일정·문구만 바뀐 정정은 원공시가 악재여도 해당없음이다.

공시 목록:
{filings}

각 공시마다 아래 형식의 객체를 담은 JSON 배열로만 답하라:
{{"rcept_no": "<접수번호>", "severe": true 또는 false, "category": "<{categories} 중 하나>", "reason": "<40자 이내 근거>"}}"""

BODY_CHARS = 2500
NO_BODY = "(본문 없음 - 제목으로만 판단)"


def base_title(report_nm: str) -> str:
    """Title without correction prefixes like [기재정정] or spacing noise."""
    return re.sub(r"^(\[[^\]]*\]\s*)+", "", report_nm).strip()


def classify_title(report_nm: str) -> str:
    """"severe", "review" or "neutral" from the title alone. Severe wins
    over review when a title matches both."""
    title = base_title(report_nm)
    if any(k in title for k in SEVERE_KEYWORDS):
        return "severe"
    if any(k in title for k in REVIEW_KEYWORDS):
        return "review"
    # A major-events report whose subtype was not given in parentheses
    # could be anything - read it.
    if title.startswith("주요사항보고서") and "(" not in title:
        return "review"
    return "neutral"


def window(trade_date: str, days: int = WINDOW_DAYS) -> tuple[str, str]:
    """(bgn_de, end_de) in DART's YYYYMMDD: the `days` before trade_date,
    ending the day before it - the decision on trade_date could only have
    seen filings received before that day."""
    d = date.fromisoformat(trade_date)
    start = d - timedelta(days=days)
    end = d - timedelta(days=1)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def body_text(raw_xml: str, limit: int = BODY_CHARS) -> str:
    """Plain text of a DART document, tags stripped, first `limit` chars."""
    text = re.sub(r"<[^>]+>", " ", raw_xml)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def build_prompt(symbol: str, name: str, filings: list[dict]) -> str:
    """filings: [{rcept_no, rcept_dt, report_nm, body}] for one symbol."""
    blocks = [
        f"[{f['rcept_no']}] {f['rcept_dt']} {f['report_nm']}\n{f['body']}"
        for f in filings
    ]
    return PROMPT_TEMPLATE.format(
        name=name, symbol=symbol, filings="\n\n".join(blocks),
        categories=", ".join(CATEGORIES))


def parse_reply(text: str, rcept_nos: list[str]) -> dict[str, dict]:
    """{rcept_no: {severe, category, reason}} for the filings asked about.

    Tolerates a ```json fence or prose around the array; anything else
    malformed raises ValueError rather than guessing - a filing missing
    from the reply is simply absent from the result (recorded as not
    judged, never as "not severe").
    """
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        raise ValueError(f"no JSON array in reply: {text[:200]!r}")
    items = json.loads(match.group(0))
    if not isinstance(items, list):
        raise ValueError("reply is not a JSON array")

    out = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        no = str(item.get("rcept_no", ""))
        severe = item.get("severe")
        category = item.get("category")
        if no not in rcept_nos or not isinstance(severe, bool):
            continue
        if category not in CATEGORIES:
            category = "해당없음" if not severe else None
        out[no] = {"severe": severe, "category": category,
                   "reason": str(item.get("reason", ""))[:200]}
    return out


def symbol_flags(rows: list[dict]) -> dict[str, bool | None]:
    """R1/R2/L for one symbol from its filing rows
    ({title_class, llm_severe}). L is None when a review filing went
    unjudged (LLM failure) and nothing else already decided it."""
    classes = {r["title_class"] for r in rows}
    r1 = "severe" in classes
    r2 = r1 or "review" in classes
    reviews = [r["llm_severe"] for r in rows if r["title_class"] == "review"]
    if r1 or any(v is True for v in reviews):
        llm = True
    elif any(v is None for v in reviews):
        llm = None
    else:
        llm = False
    return {"R1": r1, "R2": r2, "L": llm}
