"""Read-only environment check. Never places an order."""

import json
import logging
import sys

from quant import logging_config, market
from quant.toss_client import TossApiError, TossClient


def show(label: str, fn) -> None:
    print(f"\n=== {label} ===")
    try:
        result = fn()
        print(json.dumps(result, ensure_ascii=False, indent=2)[:800])
    except TossApiError as e:
        print(f"FAILED: {e}")


def main() -> None:
    logging_config.setup(logging.DEBUG if "-v" in sys.argv else logging.INFO)
    client = TossClient()
    print(f"dry_run = {client.dry_run}")

    show("accounts", client.list_accounts)
    show("buying power", client.buying_power)
    show("holdings", client.holdings)
    show("price 005930", lambda: client.price("005930"))

    calendar = client.market_calendar("US")
    show("market calendar US", lambda: calendar)
    print(f"\ncurrent session: {market.current_session(calendar)}")


if __name__ == "__main__":
    main()