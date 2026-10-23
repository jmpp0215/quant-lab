"""Desktop and phone notifications, shared by every unattended entry point.

Cron jobs (daily.py, check_due.py) run with nobody watching stdout, so a
silent failure looks identical to "nothing happened" - notify() exists so
that distinction is never left to a log file the user has to remember to
check.

Each notification also goes to the user's phone as an iMessage when
IMESSAGE_TO (a phone number or Apple ID email) is set in .env - sent by
this Mac's Messages app, so nothing passes through a third party. The
first send asks for Automation permission (Messages); run
`python -c "from quant.notify import notify; notify('quant-lab', 'test')"`
once from Terminal to grant it.

ping() reports the daily run to an external dead man's switch
(HEALTHCHECK_PING_URL, e.g. a healthchecks.io check): when the Mac is
asleep or off, nothing here can send anything, so the missing ping is
what alerts the phone. Only the ping itself leaves the machine - no
account data.
"""

import logging
import os
import re
import subprocess

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)


def _quote(text: str) -> str:
    """Escape a string for use inside an AppleScript double-quoted literal.

    AppleScript string literals only understand double quotes and a
    backslash escape, so Python's repr() is not a safe way to quote them.
    """
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def imessage_script(to: str, text: str) -> str:
    """AppleScript sending `text` to `to` over the Mac's iMessage account."""
    return ("tell application \"Messages\"\n"
            "  set s to 1st account whose service type = iMessage\n"
            f"  send {_quote(text)} to participant {_quote(to)} of s\n"
            "end tell")


ACCOUNT_KO = {"toss-bot": "토스", "kis-isa": "ISA", "kis-main": "KIS 국내",
              "kis-main-overseas": "KIS 해외"}
PHONE_MAX = 300


def _problem_ko(text: str) -> str:
    """One daily.py problem string -> a short Korean line."""
    m = re.match(r"\[(?P<who>[^\]]+)\]\s*(?P<what>.*)", text)
    who, what = (m["who"], m["what"]) if m else ("", text)
    name = ACCOUNT_KO.get(who, who)
    if who == "upcoming":
        return "일정·미리보기 계산 실패"
    if who == "backup":
        return "DB 백업 실패"
    if who == "news risk":
        return "위험 뉴스 요약 실패"
    if who == "price history":
        return "위기 재생용 가격 이력 갱신 실패"
    if who == "overseas realized":
        return "해외 실현손익 조회 실패"
    lines = []
    for part in what.split("; "):
        if part.startswith("tranche books disagree"):
            n = part.count(":") - 1
            lines.append(f"장부와 실제 잔고가 {n}종목 다름 (리밸런싱 전 정리 필요)")
        elif part.startswith("unrecorded transfer?"):
            n = len(re.findall(r"\d{4}-\d{2}-\d{2}", part))
            lines.append(f"기록 안 된 입출금 의심 {n}건 → 트래커에서 확인")
        elif part.startswith("api error"):
            lines.append("증권사 API 오류")
        else:
            lines.append(part[:60])
    return f"{name}: " + ", ".join(lines) if name else ", ".join(lines)


def phone_text(message: str) -> str:
    """The iMessage version of a notification: short Korean lines, the full
    text stays in the desktop notification and the logs."""
    if message.startswith("daily.py 실패"):
        body = "daily 실패: 시세·휴장일 조회 오류 (오늘 기록 없음)"
    elif message.startswith("daily.py: "):
        rest = message[len("daily.py: "):]
        problems = rest.split(" / ") if rest.startswith("[") else [rest]
        body = "\n".join("• " + (_problem_ko(p) if p.startswith("[") else p[:80]) for p in problems)
        body = "daily 점검 결과\n" + body
    else:
        body = message
    return body if len(body) <= PHONE_MAX else body[:PHONE_MAX - 1] + "…"


def notify(title: str, message: str) -> None:
    """Best-effort notification; never the reason a run fails."""
    script = (f"display notification {_quote(message)} "
              f"with title {_quote(title)}")
    try:
        subprocess.run(["osascript", "-e", script], timeout=5, check=False)
    except Exception:
        log.debug("notification failed", exc_info=True)

    to = os.environ.get("IMESSAGE_TO")
    if to:
        try:
            subprocess.run(["osascript", "-e", imessage_script(to, f"[{title}] {phone_text(message)}")],
                           timeout=20, check=False)
        except Exception:
            log.warning("iMessage notification failed", exc_info=True)


def ping(ok: bool = True) -> None:
    """Tell the dead man's switch the daily run happened (and how it went).
    A no-op without HEALTHCHECK_PING_URL; never the reason a run fails."""
    url = os.environ.get("HEALTHCHECK_PING_URL")
    if not url:
        return
    try:
        requests.get(url if ok else url.rstrip("/") + "/fail", timeout=10)
    except Exception:
        log.warning("health ping failed", exc_info=True)
