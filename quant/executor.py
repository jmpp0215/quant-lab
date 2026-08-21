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
from dataclasses import replace
from decimal import Decimal

from quant import config, market
from quant.broker import OrderHandle, Touch
from quant.kis_client import KisApiError
from quant.rebalance import Order
from quant.toss_client import TossApiError

log = logging.getLogger(__name__)

# Either broker's transport failure. Both are raised for "the request did
# not succeed", which is all this module needs to distinguish.
ApiError = (TossApiError, KisApiError)

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


def cancel_open_orders(broker, client) -> int:
    """Clear the book of our own resting orders before planning.

    Recomputing from current holdings is only correct if nothing of ours
    is still working.
    """
    resting = broker.open_orders(client)
    for entry in resting:
        broker.cancel(client, entry.handle)
        log.info("cancelled resting order %s", entry.handle.order_id)
    return len(resting)


def wait_for_fill(broker, client, handle: OrderHandle,
                  timeout: int | None = None) -> tuple[bool, dict]:
    """Poll until the order leaves the open list, or time runs out.

    Returns (filled, execution). The execution block carries the average
    fill price plus commission and tax where the broker computes them for
    us - worth capturing at the time rather than reconstructing later.
    """

    timeout = FILL_TIMEOUT if timeout is None else timeout
    deadline = time.time() + timeout
    last_execution: dict = {}

    while time.time() < deadline:
        resting = broker.open_orders(client)
        match = next(
            (o for o in resting if o.handle.order_id == handle.order_id), None
        )

        if match is None:
            log.info("order %s no longer open", handle.order_id)
            return True, last_execution

        if match.filled_quantity:
            last_execution = {"filledQuantity": str(match.filled_quantity)}
            log.info("order %s partially filled: %s/%s",
                     handle.order_id, match.filled_quantity, match.quantity)

        time.sleep(POLL_INTERVAL)

    log.warning("order %s still open after %ds", handle.order_id, timeout)
    return False, last_execution


def place(broker, client, order: Order, last: Decimal) -> OrderHandle | None:
    """Send one order at the touch. Returns the handle, or None if skipped."""
    touch = broker.orderbook(client, order.symbol)
    price = limit_price_for(order, touch, last)
    check_depth(order, touch)

    log.info("placing %s %d %s @ %s (last %s)",
             order.side, order.quantity, order.symbol, price, last)

    return broker.place_order(client, replace(order, limit_price=price), price)


def fetch_execution(broker, client, handle: OrderHandle) -> dict:
    """Read the final execution block once the order has left the book."""
    try:
        return broker.execution_for(client, handle)
    except ApiError as e:
        log.warning("could not read execution for %s: %s", handle.order_id, e)
        return {}


def execute(broker, client, orders: list[Order],
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
        handle: OrderHandle | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            if remaining <= 0:
                break

            try:
                handle = place(broker, client,
                               replace(order, quantity=remaining),
                               prices[order.symbol])
            except ExecutionError as e:
                log.error("skipping %s: %s", order.symbol, e)
                break

            if handle is None:      # dry run
                total_filled = order.quantity
                remaining = 0
                break

            filled, last_execution = wait_for_fill(broker, client, handle)

            if filled:
                execution = (fetch_execution(broker, client, handle)
                             or last_execution)
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
            broker.cancel(client, handle)
            execution = fetch_execution(broker, client, handle)
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
            "order_id": handle.order_id if handle else None,
            "execution": last_execution,
            "filled_quantity": total_filled,
        }

    return results