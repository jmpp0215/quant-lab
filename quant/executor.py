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
from datetime import datetime
from decimal import Decimal
from enum import Enum

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

# How long a cancelled order may take to be confirmed closed before the
# run halts. KRX confirms in seconds; this is generous on purpose.
CANCEL_TIMEOUT = 60

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


def now_hhmm() -> str:
    """The KRX clock, for the per-order auction check in execute()."""
    return datetime.now(market.KST).strftime("%H:%M")


class OrderState(Enum):
    OPEN = "open"
    PARTIALLY_FILLED = "partially filled"
    CANCEL_PENDING = "cancel pending"
    CANCELED = "canceled"            # closed short of full; may hold fills
    FILLED = "filled"
    UNKNOWN = "unknown"


# Only these states carry a filled quantity the broker will not revise.
# Everything the retry loop sizes from must come from one of them.
TERMINAL = (OrderState.FILLED, OrderState.CANCELED)


class ExecutionHalted(ExecutionError):
    """An order's final state could not be confirmed, or a broker call
    failed while placing one.

    results holds every symbol settled before the halt, plus the halted
    symbol's fills from earlier attempts that were confirmed - what the
    caller should record. The unconfirmed attempt is left for
    tranche.reconcile to surface on the next run.
    """

    def __init__(self, message: str, results: dict[str, dict]):
        super().__init__(message)
        self.results = results


def classify(status, placed: int, cancel_sent: bool) -> OrderState:
    """The state of one order from what the broker reported about it.

    placed is the quantity we sent. A status that disagrees with it, or
    reports more filled than ordered, is a response we do not understand
    and reads as UNKNOWN.
    """
    if (status is None or status.quantity != placed
            or not 0 <= status.filled_quantity <= placed):
        return OrderState.UNKNOWN
    if status.open:
        if cancel_sent:
            return OrderState.CANCEL_PENDING
        return (OrderState.PARTIALLY_FILLED if status.filled_quantity
                else OrderState.OPEN)
    if status.filled_quantity == placed:
        return OrderState.FILLED
    return OrderState.CANCELED


def await_terminal(broker, client, handle: OrderHandle, placed: int,
                   timeout: int, cancel_sent: bool):
    """Poll the order until it reaches a terminal state or time runs out.

    Returns (state, status) as last read. A failed read counts as UNKNOWN
    and is retried until the deadline, since the order may simply not be
    visible yet.
    """
    deadline = time.time() + timeout
    while True:
        try:
            status = broker.order_status(client, handle)
        except ApiError as e:
            log.warning("could not read order %s: %s", handle.order_id, e)
            status = None

        state = classify(status, placed, cancel_sent)
        if state in TERMINAL:
            log.info("order %s %s: %d/%d", handle.order_id, state.value,
                     status.filled_quantity, placed)
            return state, status
        if state is OrderState.PARTIALLY_FILLED:
            log.info("order %s partially filled: %d/%d", handle.order_id,
                     status.filled_quantity, placed)

        if time.time() >= deadline:
            log.warning("order %s still %s after %ds", handle.order_id,
                        state.value, timeout)
            return state, status
        time.sleep(POLL_INTERVAL)


def request_cancel(broker, client, handle: OrderHandle) -> None:
    """Send a cancel. A refusal is logged, not raised: the usual reason is
    that the order already filled, and the status read that follows is
    what decides."""
    try:
        broker.cancel(client, handle)
    except ApiError as e:
        log.warning("cancel of %s refused: %s", handle.order_id, e)


def cancel_open_orders(broker, client) -> int:
    """Clear the book of our own resting orders before planning.

    Recomputing from current holdings is only correct if nothing of ours
    is still working, so each cancel must be confirmed closed by the
    broker, not just accepted. broker is None for an account that can be
    snapshotted but never traded (e.g. kis-main-overseas) - nothing to
    clear there.
    """
    if broker is None:
        return 0

    resting = broker.open_orders(client)
    for entry in resting:
        request_cancel(broker, client, entry.handle)
        log.info("cancel sent for resting order %s", entry.handle.order_id)
    for entry in resting:
        state, _ = await_terminal(broker, client, entry.handle,
                                  entry.quantity, CANCEL_TIMEOUT,
                                  cancel_sent=True)
        if state not in TERMINAL:
            raise ExecutionError(
                f"resting order {entry.handle.order_id} is still "
                f"{state.value} after cancelling; holdings are not final"
            )
    return len(resting)


def place(broker, client, order: Order, last: Decimal) -> OrderHandle | None:
    """Send one order at the touch. Returns the handle, or None if skipped."""
    touch = broker.orderbook(client, order.symbol)
    price = limit_price_for(order, touch, last)
    check_depth(order, touch)

    log.info("placing %s %d %s @ %s (last %s)",
             order.side, order.quantity, order.symbol, price, last)

    return broker.place_order(client, replace(order, limit_price=price), price)


def combine_executions(fills: list[tuple[int, dict]]) -> dict:
    """One execution block for an order that filled across attempts:
    total shares, share-weighted average price, summed fees.

    rebalance_run.record() stores this block as the order's fill, and
    storage.unexplained_cash_change nets it out of the day's cash move, so
    keeping only the last attempt's block under-records the trade (2026-10-01:
    379790 filled 74 + 2, recorded as 2 @ 16,957). A fee no attempt
    reported stays absent rather than becoming a recorded 0.
    """
    shares = sum(got for got, _ in fills)
    priced = [(got, Decimal(ex["averageFilledPrice"]))
              for got, ex in fills if ex.get("averageFilledPrice")]
    combined = dict(fills[-1][1])
    combined["filledQuantity"] = str(shares)
    if priced:
        weight = sum(got for got, _ in priced)
        avg = sum(got * price for got, price in priced) / weight
        combined["averageFilledPrice"] = str(avg.quantize(Decimal("0.0001")))
    for fee in ("commission", "tax"):
        amounts = [Decimal(ex[fee]) for _, ex in fills
                   if ex.get(fee) is not None]
        if amounts:
            combined[fee] = str(sum(amounts))
        else:
            combined.pop(fee, None)
    return combined


def execute(broker, client, orders: list[Order],
            prices: dict[str, Decimal]) -> dict[str, dict]:
    """Send orders in list order, waiting for each fill before the next.

    An unfilled order is cancelled and reissued for the *remaining*
    quantity only. Reissuing the full size would double up on whatever
    already filled, and partial fills are the normal case rather than the
    exception: the touch frequently holds a small fraction of what the
    order needs.

    The remainder is sized only from a terminal state the broker
    confirmed - FILLED, or CANCELED with its cumulative fill. An accepted
    cancel request is not that: fills can land until the broker closes
    the order. If no terminal state can be read, ExecutionHalted stops
    the whole run, since later orders (buys after sells) are sized from
    these results.
    """
    results: dict[str, dict] = {}

    for order in orders:
        remaining = order.quantity
        total_filled = 0
        last_execution: dict = {}
        fills: list[tuple[int, dict]] = []   # (shares, execution) per attempt
        handle: OrderHandle | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            if remaining <= 0:
                break

            # Checked before every send, not just at start: a run begun at
            # 15:10 can still be placing orders past 15:20.
            if auction_imminent(now_hhmm()):
                results[order.symbol] = _result(remaining, handle, fills,
                                                last_execution, total_filled)
                raise ExecutionHalted(
                    f"{order.symbol}: closing auction has begun; no new "
                    "orders",
                    results,
                )

            try:
                handle = place(broker, client,
                               replace(order, quantity=remaining),
                               prices[order.symbol])
            except ExecutionError as e:
                log.error("skipping %s: %s", order.symbol, e)
                break
            except ApiError as e:
                # A failed book read sent nothing, but a failed place_order
                # may or may not have reached the exchange. Either way stop,
                # handing back every fill confirmed so far for the caller
                # to record.
                results[order.symbol] = _result(remaining, handle, fills,
                                                last_execution, total_filled)
                raise ExecutionHalted(
                    f"{order.symbol}: broker error while placing: {e}; "
                    "check the broker app for an order this run did not "
                    "see",
                    results,
                ) from e

            if handle is None:      # dry run
                total_filled = order.quantity
                remaining = 0
                break

            placed = remaining
            state, status = await_terminal(broker, client, handle, placed,
                                           FILL_TIMEOUT, cancel_sent=False)

            if state not in TERMINAL:
                # Timed out. Cancel, then wait for the broker to confirm the
                # order closed - only its final fill sizes the reprice.
                request_cancel(broker, client, handle)
                state, status = await_terminal(broker, client, handle, placed,
                                               CANCEL_TIMEOUT,
                                               cancel_sent=True)

            if state not in TERMINAL:
                results[order.symbol] = _result(remaining, handle, fills,
                                                last_execution, total_filled)
                raise ExecutionHalted(
                    f"{order.symbol}: order {handle.order_id} is "
                    f"{state.value} after cancelling; its fill is not "
                    "known, so nothing more can be sized safely",
                    results,
                )

            got = status.filled_quantity
            total_filled += got
            remaining -= got
            last_execution = status.execution
            if got:
                fills.append((got, status.execution))

            log.info("%s: %d of %d filled after attempt %d, %d remaining",
                     order.symbol, total_filled, order.quantity, attempt,
                     remaining)

        results[order.symbol] = _result(remaining, handle, fills,
                                        last_execution, total_filled)

    return results


def _result(remaining: int, handle: OrderHandle | None,
            fills: list[tuple[int, dict]], last_execution: dict,
            total_filled: int) -> dict:
    return {
        "filled": remaining <= 0,
        "order_id": handle.order_id if handle else None,
        "execution": combine_executions(fills) if len(fills) > 1
                     else last_execution,
        "filled_quantity": total_filled,
    }
