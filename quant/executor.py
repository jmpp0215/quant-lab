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
from dataclasses import dataclass, replace
from decimal import Decimal

from quant import config
from quant import market
from quant.rebalance import Order
from quant.toss_client import TossApiError, TossClient

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
TICK_BUFFER = 1


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
    """Price that should fill immediately against the current book.

    Priced a couple of ticks through the touch rather than at it: the best
    price often holds far fewer shares than the order needs, and a limit
    exactly at the touch leaves the remainder resting while the book moves
    away. The deviation check below still caps how far this can go.
    """
    price = touch.ask if order.side == "BUY" else touch.bid
    side_name = "ask" if order.side == "BUY" else "bid"

    if price is None:
        raise ExecutionError(f"{order.symbol}: no {side_name} in the book")

    tick = market.kr_tick_size(price, is_etf=config.is_etf(order.symbol))
    if order.side == "BUY":
        price = price + tick * TICK_BUFFER
    else:
        price = price - tick * TICK_BUFFER

    deviation = abs(price - last) / last
    if deviation > MAX_DEVIATION:
        raise ExecutionError(
            f"{order.symbol}: {side_name} {price} is {deviation:.1%} from "
            f"last {last}; refusing to trade into an abnormal book"
        )

    if not market.is_valid_kr_price(price, is_etf=config.is_etf(order.symbol)):
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
                  timeout: int | None = None) -> tuple[bool, dict]:
    """Poll until the order leaves the open list, or time runs out.

    Returns (filled, execution). The execution block carries the average
    fill price plus commission and tax, which the broker computes for us -
    worth capturing at the time rather than reconstructing later.
    """

    timeout = FILL_TIMEOUT if timeout is None else timeout
    deadline = time.time() + timeout
    last_execution: dict = {}

    while time.time() < deadline:
        entries = client.list_orders("OPEN")["result"]["orders"]
        match = next((o for o in entries if o["orderId"] == order_id), None)

        if match is None:
            log.info("order %s no longer open", order_id)
            return True, last_execution

        last_execution = match.get("execution") or {}
        filled = int(last_execution.get("filledQuantity") or 0)
        if filled:
            log.info("order %s partially filled: %s/%s",
                     order_id, filled, match["quantity"])

        time.sleep(POLL_INTERVAL)

    log.warning("order %s still open after %ds", order_id, timeout)
    return False, last_execution

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

def fetch_execution(client: TossClient, order_id: str) -> dict:
    """Read the final execution block from the closed order list."""
    try:
        entries = client.list_orders("CLOSED")["result"]["orders"]
    except TossApiError as e:
        log.warning("could not read closed orders: %s", e)
        return {}

    match = next((o for o in entries if o["orderId"] == order_id), None)
    return (match or {}).get("execution") or {}

def execute(client: TossClient, orders: list[Order],
            prices: dict[str, Decimal]) -> dict[str, dict]:
    """Send orders in list order, waiting for each fill before the next.

    An unfilled order is cancelled and reissued for the *remaining*
    quantity only. Reissuing the full size would double up on whatever
    already filled, and partial fills are the normal case rather than the
    exception: the touch frequently holds a small fraction of what the
    order needs.
    """
    results: dict[str, dict] = {}

    for order in orders:
        remaining = order.quantity
        total_filled = 0
        last_execution: dict = {}
        order_id: str | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            if remaining <= 0:
                break

            try:
                order_id = place(client, replace(order, quantity=remaining),
                                 prices[order.symbol])
            except ExecutionError as e:
                log.error("skipping %s: %s", order.symbol, e)
                break

            if order_id is None:      # dry run
                total_filled = order.quantity
                remaining = 0
                break

            filled, last_execution = wait_for_fill(client, order_id)

            if filled:
                execution = fetch_execution(client, order_id) or last_execution
                got = int(execution.get("filledQuantity") or remaining)
                total_filled += got
                remaining -= got
                last_execution = execution
                if remaining > 0:
                    log.info("%s: %d of %d filled, retrying the rest",
                             order.symbol, total_filled, order.quantity)
                continue

            # Timed out. Cancel, read what actually filled, and reprice the
            # remainder against a fresh book.
            client.cancel_order(order_id)
            execution = fetch_execution(client, order_id)
            got = int(execution.get("filledQuantity") or 0)
            total_filled += got
            remaining -= got
            if execution:
                last_execution = execution

            log.info("%s: %d of %d filled after attempt %d, %d remaining",
                     order.symbol, total_filled, order.quantity, attempt,
                     remaining)

        results[order.symbol] = {
            "filled": remaining <= 0,
            "order_id": order_id,
            "execution": last_execution,
            "filled_quantity": total_filled,
        }

    return results