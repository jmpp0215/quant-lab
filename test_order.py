"""Live order path test. Places an unfillable limit order, then cancels it."""

import json
import time
from decimal import Decimal

from toss_client import TossApiError, TossClient

SYMBOL = "QCOM"


def dump(label: str, data: dict) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:800])


def main() -> None:
    client = TossClient()
    print(f"dry_run = {client.dry_run}")

    # 1. Check the current price so we can pick a price far below it.
    price_data = client.price(SYMBOL)
    dump("current price", price_data)

    last = Decimal(price_data["result"][0]["lastPrice"])
    bid = (last * Decimal("0.5")).quantize(Decimal("0.01"))
    print(f"\nlast={last} -> placing BUY LIMIT at {bid}")

    input("\nPress Enter to place the order, or Ctrl+C to abort... ")

    # 2. Place the order.
    created = client.create_order(
        symbol=SYMBOL, side="BUY", order_type="LIMIT",
        quantity=1, price=str(bid),
    )
    dump("order created", created)

    if client.dry_run:
        print("\ndry_run is on. Nothing was actually sent.")
        return

    order_id = created["result"]["orderId"]
    print(f"\norderId = {order_id}")

    # 3. Confirm it shows up as an open order.
    time.sleep(2)
    dump("open orders", client.list_orders("OPEN"))

    input("\nPress Enter to cancel the order... ")

    # 4. Cancel.
    dump("cancel result", client.cancel_order(order_id))

    # 5. Confirm it is gone.
    time.sleep(2)
    dump("open orders after cancel", client.list_orders("OPEN"))


if __name__ == "__main__":
    try:
        main()
    except TossApiError as e:
        print(f"\nAPI ERROR: {e}")
    except KeyboardInterrupt:
        print("\naborted")