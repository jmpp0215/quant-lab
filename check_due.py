"""Report whether a tranche is scheduled to rebalance today.

Runs in the morning so there is time to act during the session. Places no
orders: rebalancing stays a manual step until the process has been through
several live cycles.
"""

import logging
import subprocess
import sys
from datetime import datetime

from quant import candles
from quant import config
from quant import logging_config
from quant import market
from quant import storage
from quant import tranche
from quant.toss_client import TossApiError, TossClient

ACCOUNT = "toss-bot"

log = logging.getLogger("check-due")


def notify(title: str, message: str) -> None:
    """Best-effort desktop notification; never the reason a run fails.

    AppleScript string literals only understand double quotes and a
    backslash escape, so Python's repr() is not a safe way to quote them.
    """
    def quote(text: str) -> str:
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    script = (f"display notification {quote(message)} "
              f"with title {quote(title)}")
    try:
        subprocess.run(["osascript", "-e", script], timeout=5, check=False)
    except Exception:
        log.debug("notification failed", exc_info=True)


def main() -> int:
    logging_config.setup()

    try:
        client = TossClient()
        calendar = client.market_calendar("KR")

        if not market.is_business_day(calendar):
            log.info("market closed today")
            return 0

        today = datetime.now().astimezone().date().isoformat()
        dated = candles.get(client, next(iter(config.UNIVERSE)),
                            days=config.HISTORY_DAYS, include_today=True)
        day_index = tranche.trading_day_index(dated, today)

        if day_index is None:
            log.info("%s not found in candle history", today)
            return 0

        with storage.connect() as conn:
            done = storage.tranches_done_this_month(conn, ACCOUNT, today)

        which = tranche.due_today(day_index, done, today[:7])

        if which is None:
            log.info("trading day %d: no tranche due (done: %s)",
                     day_index + 1, sorted(done) or "none")
            return 0

        log.info("trading day %d: tranche %d is due", day_index + 1, which)
        notify("quant-lab",
               f"트랜치 {which} 리밸런싱 예정 (거래일 {day_index + 1})")

    except TossApiError as e:
        log.error("api error: %s", e)
        return 1
    except Exception:
        log.exception("check failed")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())