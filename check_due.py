"""Report whether a tranche is scheduled to rebalance today.

    python check_due.py                    toss-bot (default)
    python check_due.py --account kis-isa

Runs in the morning so there is time to act during the session. Places no
orders: rebalancing stays a manual step until the process has been through
several live cycles.
"""

import logging
import sys
from datetime import datetime

from quant import accounts, candles, config, logging_config, market, storage, tranche
from quant.notify import notify
from quant.toss_client import TossApiError, TossClient

log = logging.getLogger("check-due")


def main() -> int:
    logging_config.setup()
    account, _ = accounts.extract_account(sys.argv[1:])

    try:
        # Candle/calendar data is shared market data, not account state -
        # Toss is the source regardless of which account's tranche
        # schedule we're checking.
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
            done = storage.tranches_done_this_month(conn, account, today)

        which = tranche.due_today(day_index, done, today[:7])

        if which is None:
            log.info("trading day %d: no tranche due (done: %s)",
                     day_index + 1, sorted(done) or "none")
            return 0

        log.info("trading day %d: tranche %d is due", day_index + 1, which)
        notify("quant-lab",
               f"[{account}] 트랜치 {which} 리밸런싱 예정 (거래일 {day_index + 1})")

    except TossApiError as e:
        log.error("api error: %s", e)
        # A silent failure here reads identically to "no tranche due today"
        # - the whole point of this notification is to break that tie.
        notify("quant-lab", f"[{account}] check_due 실패 (api error): {e}")
        return 1
    except Exception as e:
        log.exception("check failed")
        notify("quant-lab", f"[{account}] check_due 실패: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())