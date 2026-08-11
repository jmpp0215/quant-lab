"""Read-only environment check. Never places an order."""

import json

from toss_client import TossClient, TossApiError


def show(label: str, fn) -> None:
    print(f"\n=== {label} ===")
    try:
        result = fn()
        print(json.dumps(result, ensure_ascii=False, indent=2)[:800])
    except TossApiError as e:
        print(f"FAILED: {e}")


def main() -> None:
    client = TossClient()

    show("accounts", client.list_accounts)
    show("buying power", client.buying_power)
    show("holdings", client.holdings)
    show("price 005930", lambda: client.price("005930"))
    show("buying power USD", lambda: client.buying_power("USD"))
    show("dry run order", lambda: client.create_order(
        symbol="QCOM", side="BUY", order_type="LIMIT",
        quantity=1, price="50.00",
    ))
if __name__ == "__main__":
    main()