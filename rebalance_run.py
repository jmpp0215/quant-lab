"""Manual rebalance run.

    python rebalance_run.py                    toss-bot (default), places live orders
    python rebalance_run.py --account kis-isa   plan only - see note below

Capital is split across tranches that rebalance on different trading days
of the month, so a run touches one sleeve and leaves the others alone.
Only that sleeve's holdings and its share of the cash pool are in scope.

executor.py does now support KIS, but live order placement is still
deliberately limited to toss-bot: the KIS execution path has never sent a
real order, and KIS has no paper environment in which to prove it. So for
any non-toss-bot account this script computes and prints the rebalance
plan the same way, then stops before sending anything. Lifting that means
editing the guard below - see RUNBOOK.md.
"""

import logging
import sys
from datetime import datetime
from decimal import Decimal

from quant import (
    accounts,
    candles,
    config,
    executor,
    logging_config,
    market,
    rebalance,
    storage,
    strategy,
    tranche,
)
from quant.toss_client import TossClient

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
    client = cfg["client"]()
    log.info("account = %s, dry_run = %s", account, client.dry_run)

    # Candle/calendar data is shared market data, not account state - Toss
    # is the source regardless of which account is being rebalanced.
    # candles.fetch() also relies on a Toss-shaped client.get(path, params)
    # call, which KisClient's get() (which requires a tr_id) does not
    # support, so a KIS account still needs a Toss client alongside it.
    #
    # Reuse rather than construct when the account is itself Toss: Toss
    # keeps only one active token per credential set, so a second client
    # invalidates the first one's token the moment it authenticates, and
    # whichever client is used next fails with 401 mid-run.
    market_client = client if isinstance(client, TossClient) else TossClient()

    now = datetime.now().astimezone()
    calendar = market_client.market_calendar("KR")

    if not market.is_business_day(calendar):
        log.error("market closed today")
        return 1
    if market.current_session(calendar) != "regularMarket":
        log.error("outside the regular session")
        return 1
    if executor.auction_imminent(now.strftime("%H:%M")):
        log.error("closing auction has begun; no new orders")
        return 1

    if account == "toss-bot":
        executor.cancel_open_orders(cfg["broker"], client)
    else:
        # executor supports KIS now, but this flips together with the
        # order-placement guard below, not before it.
        log.info("%s: skipping open-order cleanup until live trading "
                 "is enabled for this account", account)

    data = {
        sym: candles.get(market_client, sym, days=config.HISTORY_DAYS)
        for sym in config.all_symbols()
    }
    trade_date = data[next(iter(config.UNIVERSE))][0]["timestamp"][:10]

    # Schedule is keyed to the actual calendar day, not the signal date:
    # the signal lags by design, but the rebalance happens today.
    dated = candles.get(market_client, next(iter(config.UNIVERSE)),
                        days=config.HISTORY_DAYS, include_today=True)
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

    signal = strategy.evaluate(data)
    log.info("\n%s", strategy.format_signal(signal))

    cash = snap.cash
    book = books.get(which, {})
    prices = cfg["price"](client, set(signal.weights) | set(book))

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

    if account != "toss-bot":
        log.error(
            "%s 계좌 주문은 의도적으로 막혀 있습니다 (구현이 없어서가 아님) - "
            "executor.py는 이제 KIS를 지원하지만, KIS 주문 경로로 실제 주문을 "
            "내본 적이 없고 KIS에는 페이퍼 환경도 없습니다. 위 계산된 플랜은 "
            "정상이며 참고용으로 쓸 수 있습니다. 해제하려면 rebalance_run.py의 "
            "이 가드를 제거하세요 (RUNBOOK.md 참고).",
            account,
        )
        return 1

    if not confirm(f"Rebalance tranche {which}?"):
        log.info("aborted by user")
        return 0

    sells = [o for o in orders if o.side == "SELL"]
    buys = [o for o in orders if o.side == "BUY"]
    results: dict[str, dict] = {}

    if sells:
        results |= executor.execute(cfg["broker"], client, sells, prices)

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
            results |= executor.execute(cfg["broker"], client, buys, prices)

    final_book = apply_fills(book, orders, results)

    if not client.dry_run:
        record(account, trade_date, which, orders, results, final_book)

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