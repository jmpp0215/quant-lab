"""Turn a target allocation into concrete orders.

The strategy says "one third each"; this module works out how many whole
shares that is, given current holdings, cash, and the exchange's tick
rules. Kept separate from the strategy so the strategy stays testable
without any account state.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

import config
import market

log = logging.getLogger(__name__)

# Skip trades below this notional. Tiny rebalancing trades cost more in
# commission and spread than the tracking error they correct.
MIN_ORDER_KRW = Decimal("50000")


@dataclass(frozen=True)
class Position:
    symbol: str
    name: str
    quantity: int
    last_price: Decimal

    @property
    def value(self) -> Decimal:
        return self.last_price * self.quantity


@dataclass(frozen=True)
class Order:
    symbol: str
    name: str
    side: str          # BUY | SELL
    quantity: int
    limit_price: Decimal

    @property
    def notional(self) -> Decimal:
        return self.limit_price * self.quantity


def parse_positions(holdings: dict) -> dict[str, Position]:
    """Extract KRW positions from the holdings response."""
    out = {}
    for item in holdings["result"]["items"]:
        if item["currency"] != "KRW":
            continue
        out[item["symbol"]] = Position(
            symbol=item["symbol"],
            name=item["name"],
            quantity=int(item["quantity"]),
            last_price=Decimal(item["lastPrice"]),
        )
    return out


def plan(target_weights: dict[str, Decimal],
         positions: dict[str, Position],
         prices: dict[str, Decimal],
         cash: Decimal) -> list[Order]:
    """Produce the orders that move the portfolio toward the target.

    Sells are emitted before buys so the cash they release is available;
    the caller is responsible for waiting on fills before sending buys.
    """
    total = cash + sum(p.value for p in positions.values())
    log.info("total assets: %s KRW", f"{total:,.0f}")

    sells: list[Order] = []
    buys: list[Order] = []

    symbols = set(positions) | set(target_weights)

    for symbol in sorted(symbols):
        price = prices.get(symbol) or (
            positions[symbol].last_price if symbol in positions else None
        )
        if price is None or price <= 0:
            log.warning("%s: no price available, skipping", symbol)
            continue

        weight = target_weights.get(symbol, Decimal("0"))
        target_qty = int(total * weight / price)
        held_qty = positions[symbol].quantity if symbol in positions else 0
        delta = target_qty - held_qty

        if delta == 0:
            continue

        name = config.UNIVERSE.get(
            symbol,
            positions[symbol].name if symbol in positions else symbol,
        )
        limit = market.round_to_tick(price, is_etf=config.is_etf(symbol))
        order = Order(
            symbol=symbol,
            name=name,
            side="BUY" if delta > 0 else "SELL",
            quantity=abs(delta),
            limit_price=limit,
        )

        if order.notional < MIN_ORDER_KRW:
            log.info("%s: skipping %s of %s KRW (below minimum)",
                     symbol, order.side, f"{order.notional:,.0f}")
            continue

        (buys if delta > 0 else sells).append(order)

    return sells + buys


def format_plan(orders: list[Order]) -> str:
    if not orders:
        return "no orders needed"
    lines = ["rebalance plan:"]
    for o in orders:
        lines.append(
            f"  {o.side:<4} {o.name:<24} {o.quantity:>5} @ "
            f"{o.limit_price:>10,.0f} = {o.notional:>12,.0f} KRW"
        )
    return "\n".join(lines)