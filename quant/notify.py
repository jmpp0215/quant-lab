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
            subprocess.run(["osascript", "-e", imessage_script(to, f"[{title}] {message}")],
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
