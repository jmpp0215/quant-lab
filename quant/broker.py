"""The execution interface every broker module implements.

Only dataclasses live here, and nothing in this module imports the rest of
`quant` - both client modules import it, so anything heavier would make a
cycle (executor imports the clients).

A broker module satisfies the interface by exposing five module-level
functions, the same shape as the `snapshot()` adapters:

    orderbook(client, symbol)        -> Touch
    place_order(client, order, price) -> OrderHandle | None   (None = dry run)
    open_orders(client)              -> list[OpenOrder]
    cancel(client, handle)           -> None    (request sent, not confirmed)
    order_status(client, handle)     -> OrderStatus | None    (None = no record)

`executor` is handed one of those modules and never touches a broker's
response shape itself.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Touch:
    """Best bid and ask, with the size resting at each.

    A missing side is None rather than zero: "no ask in the book" and "the
    ask is zero" are different situations, and only the first one is real.
    KIS reports an empty book as "0", so its adapter maps that to None.
    """
    bid: Decimal | None
    ask: Decimal | None
    bid_volume: int
    ask_volume: int


@dataclass(frozen=True)
class OrderHandle:
    """Whatever a broker needs to identify one live order afterwards.

    Toss identifies an order by id alone. KIS's cancel request also
    requires the KRX forwarding branch number issued when the order was
    accepted, so a bare id string cannot address a KIS order - hence a
    handle rather than a str throughout the executor.
    """
    order_id: str
    org_no: str | None = None


@dataclass(frozen=True)
class OpenOrder:
    """One of our orders still resting on the book."""
    handle: OrderHandle
    symbol: str
    quantity: int
    filled_quantity: int


@dataclass(frozen=True)
class OrderStatus:
    """What the broker reports about one order right now.

    open is the broker's own word that some quantity can still fill - an
    accepted cancel request does not clear it, only the broker confirming
    the order closed does. filled_quantity is cumulative across every
    partial fill. execution is the block storage.save_order wants
    (filledQuantity, averageFilledPrice, commission, tax).

    An adapter that cannot read a field it needs returns None for the whole
    status rather than defaulting it: a guessed quantity is exactly what
    the retry loop must never size a reorder from.
    """
    quantity: int
    filled_quantity: int
    open: bool
    execution: dict
