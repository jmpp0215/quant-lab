"""Trading bot daemon."""

import logging
import signal
import sys
import time
from datetime import datetime, timezone

import logging_config
import market
from toss_client import TossClient, TossApiError

POLL_INTERVAL = 30
CALENDAR_REFRESH = 3600

# systemd will not restart on this code, so use it for config errors that
# restarting cannot fix.
EXIT_FATAL = 3

log = logging.getLogger("bot")

_running = True


def _handle_signal(signum: int, _frame) -> None:
    global _running
    log.info("received %s, shutting down", signal.Signals(signum).name)
    _running = False


class Bot:
    def __init__(self, client: TossClient) -> None:
        self.client = client
        self._calendar: dict | None = None
        self._calendar_fetched_at: float = 0.0

    def calendar(self) -> dict:
        """Cached market calendar. Sessions only change once per day."""
        now = time.time()
        if self._calendar is None or now - self._calendar_fetched_at > CALENDAR_REFRESH:
            self._calendar = self.client.market_calendar("US")
            self._calendar_fetched_at = now
            log.info("market calendar refreshed")
        return self._calendar

    def preflight(self) -> None:
        """Fail fast on anything a restart would not fix."""
        log.info("preflight start (dry_run=%s)", self.client.dry_run)

        accounts = self.client.list_accounts()
        log.info("account check ok: %s accounts", len(accounts["result"]))

        self.client.buying_power()
        self.calendar()

        log.info("preflight passed")

    def tick(self, session: str) -> None:
        """One polling iteration. Strategy logic goes here."""
        price = self.client.price("QCOM")["result"][0]
        log.info("session=%s QCOM=%s", session, price["lastPrice"])

    def run(self) -> int:
        try:
            self.preflight()
        except (TossApiError, KeyError, RuntimeError) as e:
            log.critical("preflight failed: %s", e)
            return EXIT_FATAL

        while _running:
            try:
                session = market.current_session(self.calendar())

                if session is None:
                    log.debug("market closed, idling")
                elif session == "regularMarket":
                    self.tick(session)
                else:
                    log.debug("session=%s, skipping tick", session)

            except TossApiError as e:
                if e.status == 403:
                    log.critical("blocked (403). check the allowed IP list")
                    return EXIT_FATAL
                log.error("api error, continuing: %s", e)

            except Exception:
                log.exception("unexpected error, continuing")

            self._sleep(POLL_INTERVAL)

        log.info("stopped cleanly")
        return 0

    @staticmethod
    def _sleep(seconds: int) -> None:
        """Sleep in short slices so SIGTERM is handled promptly."""
        for _ in range(seconds):
            if not _running:
                return
            time.sleep(1)


def main() -> int:
    logging_config.setup()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log.info("bot starting")
    return Bot(TossClient()).run()


if __name__ == "__main__":
    sys.exit(main())