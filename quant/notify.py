"""Desktop notifications, shared by every unattended entry point.

Cron jobs (daily.py, check_due.py) run with nobody watching stdout, so a
silent failure looks identical to "nothing happened" - notify() exists so
that distinction is never left to a log file the user has to remember to
check.
"""

import logging
import subprocess

log = logging.getLogger(__name__)


def _quote(text: str) -> str:
    """Escape a string for use inside an AppleScript double-quoted literal.

    AppleScript string literals only understand double quotes and a
    backslash escape, so Python's repr() is not a safe way to quote them.
    """
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def notify(title: str, message: str) -> None:
    """Best-effort desktop notification; never the reason a run fails."""
    script = (f"display notification {_quote(message)} "
              f"with title {_quote(title)}")
    try:
        subprocess.run(["osascript", "-e", script], timeout=5, check=False)
    except Exception:
        log.debug("notification failed", exc_info=True)
