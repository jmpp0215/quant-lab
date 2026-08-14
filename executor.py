"""Order execution.

Orders are placed as limit orders at the touch - buys at the best ask,
sells at the best bid - rather than as market orders. A market order has
no price ceiling, so a thin book fills it at whatever is there; during
the closing auction that has produced fills far from the last traded
price. A limit at the touch fills just as readily when the book is
healthy, and simply does not fill when it is not.
"""

import logging
import time
from dataclasses import dataclass
from decimal import Decimal

import config
import market
from rebalance import Order
from toss_client import TossClient, TossApiError

log = logging.getLogger(__name__)

# Refuse to trade when the touch sits this far from the last price: the
# book is not in a state we understand.
MAX_DEVIATION = Decimal("0.02")

# How long to wait for a fill before repricing, and how many attempts.
FILL_TIMEOUT = 180
POLL_INTERVAL = 10
MAX_ATTEMPTS = 3

# No new orders once the closing auction begins.
AUCTION_START = "15:20"


class ExecutionError(RuntimeError):
    """Raised when execution cannot proceed safely."""


@dataclass(frozen=True)
class Touch:
    bid: Decimal | None
    ask: Decimal | None
    bid_volume: int
    ask_volume: int


def read_touch(client: TossClient, symbol: str) -> Touch:
    book = client.get("/api/v1/orderbook", params={"symbol": symbol})["result"]
    asks, bids = book.get("asks") or [], book.get("bids") or []
    return Touch(
        bid=Decimal(bids[0]["price"]) if bids else None,
        ask=Decimal(asks[0]["price"]) if asks else None,
        bid_volume=int(bids[0]["volume"]) if bids else 0,
        ask_volume=int(asks[0]["volume"]) if asks else 0,
    )


def limit_price_for(order: Order, touch: Touch, last: Decimal) -> Decimal:
    """Price that should fill immediately against the current book."""
    price = touch.ask if order.side == "BUY" else touch.bid
    side_name = "ask" if order.side == "BUY" else "bid"

    if price is None:
        raise ExecutionError(f"{order.symbol}: no {side_name} in the book")

    deviation = abs(price - last) / last
    if deviation > MAX_DEVIATION:
        raise ExecutionError(
            f"{order.symbol}: {side_name} {price} is {deviation:.1%} from "
            f"last {last}; refusing to trade into an abnormal book"
        )

    if not market.is_valid_kr_price(price, is_etf=config.IS_ETF):
        raise ExecutionError(f"{order.symbol}: {price} is off-tick")

    return price


def check_depth(order: Order, touch: Touch) -> None:
    """Warn when the touch cannot absorb the whole order.

    The remainder rests unfilled rather than sweeping deeper, which is the
    intended trade-off, but it is worth surfacing.
    """
    available = touch.ask_volume if order.side == "BUY" else touch.bid_volume
    if available < order.quantity:
        log.warning(
            "%s: touch holds %d of %d shares; the rest will rest unfilled",
            order.symbol, available, order.quantity,
        )


def auction_imminent(now_hhmm: str) -> bool:
    return now_hhmm >= AUCTION_START


def cancel_open_orders(client: TossClient) -> int:
    """Clear the book of our own resting orders before planning.

    Recomputing from current holdings is only correct if nothing of ours
    is still working.
    """
    open_orders = client.list_orders("OPEN")["result"]["orders"]
    for entry in open_orders:
        client.cancel_order(entry["orderId"])
        log.info("cancelled resting order %s", entry["orderId"])
    return len(open_orders)


def wait_for_fill(client: TossClient, order_id: str,
                  timeout: int = FILL_TIMEOUT) -> bool:
    """Poll until the order leaves the open list, or time runs out."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        open_ids = {
            o["orderId"]
            for o in client.list_orders("OPEN")["result"]["orders"]
        }
        if order_id not in open_ids:
            log.info("order %s filled", order_id)
            return True
        time.sleep(POLL_INTERVAL)

    log.warning("order %s still open after %ds", order_id, timeout)
    return False

def place(client: TossClient, order: Order, last: Decimal) -> str | None:
    """Send one order at the touch. Returns the order id, or None if skipped."""
    touch = read_touch(client, order.symbol)
    price = limit_price_for(order, touch, last)
    check_depth(order, touch)

    log.info("placing %s %d %s @ %s (last %s)",
             order.side, order.quantity, order.symbol, price, last)

    result = client.create_order(
        symbol=order.symbol,
        side=order.side,
        order_type="LIMIT",
        quantity=order.quantity,
        price=str(price),
    )

    if result.get("dryRun"):
        return None
    return result["result"]["orderId"]


def execute(client: TossClient, orders: list[Order],
            prices: dict[str, Decimal]) -> dict[str, bool]:
    """Send orders in list order, waiting for each fill before the next.

    Sells precede buys in the plan, and a buy cannot be funded until the
    sell settles, so the sequencing is not merely cosmetic.
    """
    results: dict[str, bool] = {}

    for order in orders:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                order_id = place(client, order, prices[order.symbol])
            except ExecutionError as e:
                log.error("skipping %s: %s", order.symbol, e)
                results[order.symbol] = False
                break

            if order_id is None:      # dry run
                results[order.symbol] = True
                break

            if wait_for_fill(client, order_id):
                results[order.symbol] = True
                break

            # Unfilled: cancel and reprice against a fresh book rather than
            # leaving a stale order resting at a price the market has left.
            log.info("repricing %s (attempt %d/%d)",
                     order.symbol, attempt, MAX_ATTEMPTS)
            client.cancel_order(order_id)
        else:
            log.error("%s: gave up after %d attempts", order.symbol,
                      MAX_ATTEMPTS)
            results[order.symbol] = False

    return results