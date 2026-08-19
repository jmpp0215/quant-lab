"""Manual rebalance run.

Capital is split across tranches that rebalance on different trading days
of the month, so a run touches one sleeve and leaves the others alone.
Only that sleeve's holdings and its share of the cash pool are in scope.
"""

import logging
import sys
from datetime import datetime
from decimal import Decimal

import candles
import config
import executor
import logging_config
import market
import rebalance
import storage
import strategy
import tranche
from toss_client import TossClient, TossApiError

ACCOUNT = "toss-bot"

log = logging.getLogger("rebalance")


def confirm(prompt: str) -> bool:
    return input(f"\n{prompt} [yes/no]: ").strip().lower() == "yes"


def current_prices(client: TossClient, symbols: set[str]) -> dict[str, Decimal]:
    if not symbols:
        return {}
    result = client.price(",".join(sorted(symbols)))["result"]
    return {r["symbol"]: Decimal(r["lastPrice"]) for r in result}


def plan_for_tranche(book: dict[str, int], targets: dict[str, int],
                     prices: dict[str, Decimal]) -> list[rebalance.Order]:
    """Orders that move one sleeve from `book` to `targets`."""
    sells, buys = [], []

    for symbol in sorted(set(book) | set(targets)):
        price = prices.get(symbol)
        if price is None or price <= 0:
            log.warning("%s: no price available, skipping", symbol)
            continue

        delta = targets.get(symbol, 0) - book.get(symbol, 0)
        if delta == 0:
            continue

        order = rebalance.Order(
            symbol=symbol,
            name=config.UNIVERSE.get(symbol, symbol),
            side="BUY" if delta > 0 else "SELL",
            quantity=abs(delta),
            limit_price=market.round_to_tick(
                price, is_etf=config.is_etf(symbol)),
        )

        if order.notional < rebalance.MIN_ORDER_KRW:
            log.info("%s: skipping %s of %s KRW (below minimum)",
                     symbol, order.side, f"{order.notional:,.0f}")
            continue

        (buys if delta > 0 else sells).append(order)

    return sells + buys


def main() -> int:
    logging_config.setup()
    storage.init()

    client = TossClient()
    log.info("dry_run = %s", client.dry_run)

    now = datetime.now().astimezone()
    calendar = client.market_calendar("KR")

    if not market.is_business_day(calendar):
        log.error("market closed today")
        return 1
    if market.current_session(calendar) != "regularMarket":
        log.error("outside the regular session")
        return 1
    if executor.auction_imminent(now.strftime("%H:%M")):
        log.error("closing auction has begun; no new orders")
        return 1

    executor.cancel_open_orders(client)

    data = {
        sym: candles.get(client, sym, days=config.HISTORY_DAYS)
        for sym in config.all_symbols()
    }
    trade_date = data[next(iter(config.UNIVERSE))][0]["timestamp"][:10]

    # Schedule is keyed to the actual calendar day, not the signal date:
    # the signal lags by design, but the rebalance happens today.
    dated = candles.get(client, next(iter(config.UNIVERSE)),
                        days=config.HISTORY_DAYS, include_today=True)
    today = now.date().isoformat()
    day_index = tranche.trading_day_index(dated, today)

    if day_index is None:
        log.error("%s is not a trading day in the candle history", today)
        return 1

    with storage.connect() as conn:
        done = storage.tranches_done_this_month(conn, ACCOUNT, today)
        books = storage.load_all_tranche_holdings(conn, ACCOUNT)

    which = tranche.due_today(day_index, done, today[:7])
    if which is None:
        log.info("trading day %d: no tranche due (done: %s)",
                 day_index + 1, sorted(done) or "none")
        return 0

    log.info("trading day %d: tranche %d is due", day_index + 1, which)

    # The books drive every quantity below, so trading through a
    # discrepancy would compound it.
    positions = rebalance.parse_positions(client.holdings())
    actual = {p.symbol: p.quantity for p in positions.values()}
    drift = tranche.reconcile(books, actual)
    if drift:
        log.error("tranche books disagree with the account: %s", drift)
        log.error("reconcile before rebalancing")
        return 1

    signal = strategy.evaluate(data)
    log.info("\n%s", strategy.format_signal(signal))

    cash = Decimal(client.buying_power("KRW")["result"]["cashBuyingPower"])
    book = books.get(which, {})
    prices = current_prices(client, set(signal.weights) | set(book))

    value = tranche.tranche_value(book, prices, cash)
    targets = tranche.target_quantities(signal.weights, value, prices)

    log.info("tranche %d: %s KRW (holdings + %s cash share)",
             which, f"{value:,.0f}", f"{tranche.cash_share(cash):,.0f}")
    log.info("current: %s", book)
    log.info("target : %s", targets)

    orders = plan_for_tranche(book, targets, prices)
    log.info("\n%s", rebalance.format_plan(orders))

    if not orders:
        log.info("nothing to do")
        return 0

    if not confirm(f"Rebalance tranche {which}?"):
        log.info("aborted by user")
        return 0

    sells = [o for o in orders if o.side == "SELL"]
    buys = [o for o in orders if o.side == "BUY"]
    results: dict[str, dict] = {}

    if sells:
        results |= executor.execute(client, sells, prices)

    if buys:
        # Recompute against the cash the sells actually raised.
        cash = Decimal(
            client.buying_power("KRW")["result"]["cashBuyingPower"])
        book_after = apply_fills(book, sells, results)
        value = tranche.tranche_value(book_after, prices, cash)
        targets = tranche.target_quantities(signal.weights, value, prices)
        buys = [o for o in plan_for_tranche(book_after, targets, prices)
                if o.side == "BUY"]

        log.info("\nrevised buy plan:\n%s", rebalance.format_plan(buys))
        if buys and confirm("Proceed with buys?"):
            results |= executor.execute(client, buys, prices)

    final_book = apply_fills(book, orders, results)

    if not client.dry_run:
        record(trade_date, which, orders, results, final_book)

    log.info("tranche %d now holds: %s", which, final_book)
    return 0


def apply_fills(book: dict[str, int], orders: list[rebalance.Order],
                results: dict[str, dict]) -> dict[str, int]:
    """Update a sleeve's book by what actually filled, not what was asked.

    A partially filled order leaves the tranche between target and start;
    recording the request instead would desync the books from the account.
    """
    updated = dict(book)
    for order in orders:
        result = results.get(order.symbol)
        if not result:
            continue
        filled = result.get("filled_quantity", 0)
        if not filled:
            continue
        delta = filled if order.side == "BUY" else -filled
        updated[order.symbol] = updated.get(order.symbol, 0) + delta
    return {s: q for s, q in updated.items() if q > 0}


def record(trade_date: str, which: int, orders: list[rebalance.Order],
           results: dict[str, dict], book: dict[str, int]) -> None:
    now = datetime.now().astimezone().isoformat()
    with storage.connect() as conn:
        for order in orders:
            r = results.get(order.symbol, {})
            storage.save_order(
                conn, trade_date, now, ACCOUNT, order.symbol, order.side,
                order.quantity, order.limit_price, r.get("order_id"),
                r.get("filled", False), r.get("execution"), tranche=which,
            )
        storage.save_tranche_holdings(conn, which, ACCOUNT, book, now)


if __name__ == "__main__":
    sys.exit(main())