"""Manual rebalance run.

    python rebalance_run.py                    kis-isa (default) - places live orders
    python rebalance_run.py --account toss-bot  sandbox account, ad hoc trials only

Capital is split across tranches that rebalance on different trading days
of the month, so a run touches one sleeve and leaves the others alone.
Only that sleeve's holdings and its share of the cash pool are in scope.

kis-isa is the live strategy account. The account != "toss-bot" guard that
used to stop this script short of sending real KIS orders was removed once
the ISA go-live checklist in RUNBOOK.md was complete, including a live
verification of executor.execute()'s retry/reprice/wait-for-fill loop
against KIS (2026-08-31) - see RUNBOOK.md for that record.
"""

import logging
import sys
from datetime import datetime
from decimal import Decimal

from quant import (
    accounts,
    candles,
    config,
    dividends,
    executor,
    logging_config,
    market,
    rebalance,
    storage,
    strategy,
    tranche,
)
from quant.kis_client import KisClient

log = logging.getLogger("rebalance")


def confirm(prompt: str) -> bool:
    return input(f"\n{prompt} [yes/no]: ").strip().lower() == "yes"


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

    account, _ = accounts.extract_account(sys.argv[1:])
    cfg = accounts.resolve(account)
    if not cfg["tradable"]:
        log.error("%s is not a tradable account for this script (see "
                 "quant/accounts.py's ACCOUNTS registry)", account)
        return 1
    client = cfg["client"]()
    log.info("account = %s, dry_run = %s", account, client.dry_run)

    # Calendar, candles and dividends are shared market data, not account
    # state - all come from KIS regardless of which account is being
    # rebalanced. Reuse `client` when it is already the KIS account,
    # otherwise stand up kis-isa's client for the market-data calls.
    market_data = client if isinstance(client, KisClient) else KisClient("isa")

    now = datetime.now().astimezone()
    holiday = market_data.holidays(now.strftime("%Y%m%d"))

    if not market.is_business_day(holiday):
        log.error("market closed today")
        return 1
    if market.current_session() != "regularMarket":
        log.error("outside the regular session")
        return 1
    if executor.auction_imminent(now.strftime("%H:%M")):
        log.error("closing auction has begun; no new orders")
        return 1

    executor.cancel_open_orders(cfg["broker"], client)

    data = {
        sym: candles.get(market_data, sym, days=config.HISTORY_DAYS,
                         source="kis")
        for sym in config.all_symbols()
    }
    trade_date = data[next(iter(config.UNIVERSE))][0]["timestamp"][:10]

    # Schedule is keyed to the actual calendar day, not the signal date:
    # the signal lags by design, but the rebalance happens today.
    dated = candles.get(market_data, next(iter(config.UNIVERSE)),
                        days=config.HISTORY_DAYS, include_today=True,
                        source="kis")
    today = now.date().isoformat()
    day_index = tranche.trading_day_index(dated, today)

    if day_index is None:
        log.error("%s is not a trading day in the candle history", today)
        return 1

    with storage.connect() as conn:
        done = storage.tranches_done_this_month(conn, account, today)
        books = storage.load_all_tranche_holdings(conn, account)

    which = tranche.due_today(day_index, done, today[:7])
    if which is None:
        log.info("trading day %d: no tranche due (done: %s)",
                 day_index + 1, sorted(done) or "none")
        return 0

    log.info("trading day %d: tranche %d is due", day_index + 1, which)

    # The books drive every quantity below, so trading through a
    # discrepancy would compound it. One snapshot() call gives both
    # positions and cash, broker-agnostic.
    snap = cfg["snapshot"](client)
    actual = {p["symbol"]: p["qty"] for p in snap.positions}
    drift = tranche.reconcile(books, actual)
    if drift:
        log.error("tranche books disagree with the account: %s", drift)
        log.error("reconcile before rebalancing")
        return 1

    # Dividend data also comes from KIS - reuse the market-data client.
    with storage.connect() as conn:
        dividends.sync_all(conn, market_data, config.all_symbols())
        dividend_events = dividends.load_all(conn, config.all_symbols())

    signal = strategy.evaluate(data, dividend_events)
    log.info("\n%s", strategy.format_signal(signal))

    book = books.get(which, {})
    prices = cfg["price"](client, set(signal.weights) | set(book))

    if not prices:
        # Only possible when this sleeve holds nothing and the signal
        # itself wants no positions - nothing to price, nothing to trade.
        log.info("tranche %d: no holdings and no signal weights, nothing "
                 "to do", which)
        return 0

    # Size against no-미수 buying power (KIS nrcvb_buy_amt / Toss available
    # cash), not settled cash: after a recent sell the settled balance
    # lags the proceeds by a day or two, which would undersize this plan.
    # The post-sell buy recompute below already uses this figure - reading
    # it here too keeps the previewed plan and the executed plan the same.
    cash = cfg["buying_power"](client, prices)

    value = tranche.tranche_value(book, prices, cash)
    targets = tranche.target_quantities(signal.weights, value, prices)

    log.info("tranche %d: %s KRW (holdings + %s cash share)",
             which, f"{value:,.0f}", f"{tranche.cash_share(cash):,.0f}")
    # buying_power and the snapshot's settled cash are read from different
    # KIS fields (see buying_power's docstring) and are not cross-checked
    # automatically - a gap here is expected for a day or two after a sell
    # (T+2 settlement), but a much larger one is worth a second look before
    # confirming.
    log.info("buying power %s KRW vs settled cash %s KRW (snapshot)",
             f"{cash:,.0f}", f"{snap.cash:,.0f}")
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
        results |= executor.execute(cfg["broker"], client, sells, prices)

    if buys:
        # Recompute against the cash the sells actually raised, via each
        # broker's own buying-power/order-possible-cash figure rather than
        # a settled-cash balance that hasn't caught up with today's sells.
        cash = cfg["buying_power"](client, prices)
        book_after = apply_fills(book, sells, results)
        value = tranche.tranche_value(book_after, prices, cash)
        targets = tranche.target_quantities(signal.weights, value, prices)
        buys = [o for o in plan_for_tranche(book_after, targets, prices)
                if o.side == "BUY"]

        log.info("\nrevised buy plan:\n%s", rebalance.format_plan(buys))
        if buys and confirm("Proceed with buys?"):
            results |= executor.execute(cfg["broker"], client, buys, prices)

    # sells + buys, not the original `orders`: buys was reassigned above to
    # the post-sell revised plan, and record() must log what was actually
    # sent to the broker, not the stale pre-resize quantities/prices.
    executed_orders = sells + buys
    final_book = apply_fills(book, executed_orders, results)

    if not client.dry_run:
        record(account, trade_date, which, executed_orders, results, final_book)

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


def record(account: str, trade_date: str, which: int,
           orders: list[rebalance.Order], results: dict[str, dict],
           book: dict[str, int]) -> None:
    now = datetime.now().astimezone().isoformat()
    today = datetime.now().astimezone().date().isoformat()
    with storage.connect() as conn:
        for order in orders:
            r = results.get(order.symbol, {})
            storage.save_order(
                conn, trade_date, now, account, order.symbol, order.side,
                order.quantity, order.limit_price, r.get("order_id"),
                r.get("filled", False), r.get("execution"), tranche=which,
                executed_date=today,
            )
        storage.save_tranche_holdings(conn, which, account, book, now)


if __name__ == "__main__":
    sys.exit(main())