"""Manual rebalance run.

Kept separate from daily.py on purpose: the daily cron job only observes,
and nothing scheduled should be able to place an order. Every step here
is confirmed by a human until the process has been through a few live
cycles.
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


def snapshot(client: TossClient) -> tuple[dict, Decimal]:
    positions = rebalance.parse_positions(client.holdings())
    cash = Decimal(client.buying_power("KRW")["result"]["cashBuyingPower"])
    return positions, cash


def record_orders(trade_date: str, orders: list[rebalance.Order],
                  results: dict[str, dict]) -> None:
    now = datetime.now().astimezone().isoformat()
    with storage.connect() as conn:
        for order in orders:
            r = results.get(order.symbol, {})
            storage.save_order(
                conn, trade_date, now, ACCOUNT, order.symbol, order.side,
                order.quantity, order.limit_price, r.get("order_id"),
                r.get("filled", False), r.get("execution"),
            )


def main() -> int:
    logging_config.setup()
    storage.init()

    client = TossClient()
    log.info("dry_run = %s", client.dry_run)

    # --- guards -------------------------------------------------------
    now = datetime.now().astimezone()
    session = market.current_session(client.market_calendar("KR"))

    if session != "regularMarket":
        log.error("KR session is %s; rebalancing needs the regular session",
                  session)
        return 1

    if executor.auction_imminent(now.strftime("%H:%M")):
        log.error("closing auction has begun; no new orders")
        return 1

    # --- clear the book ----------------------------------------------
    cancelled = executor.cancel_open_orders(client)
    if cancelled:
        log.info("cancelled %d resting orders", cancelled)

    # --- signal -------------------------------------------------------
    data = {
        sym: candles.get(client, sym, days=config.HISTORY_DAYS)
        for sym in config.all_symbols()
    }
    trade_date = data[next(iter(config.UNIVERSE))][0]["timestamp"][:10]

    with storage.connect() as conn:
        if storage.ordered_today(conn, trade_date):
            log.error("already rebalanced on %s", trade_date)
            return 1

    signal = strategy.evaluate(data)
    log.info("\n%s", strategy.format_signal(signal))

    # --- plan ---------------------------------------------------------
    positions, cash = snapshot(client)
    prices = current_prices(client, set(signal.weights) | set(positions))
    orders = rebalance.plan(signal.weights, positions, prices, cash)

    log.info("\n%s", rebalance.format_plan(orders))
    if not orders:
        log.info("nothing to do")
        return 0

    if not confirm("Proceed with this plan?"):
        log.info("aborted by user")
        return 0

    # --- sells --------------------------------------------------------
    sells = [o for o in orders if o.side == "SELL"]
    results: dict[str, bool] = {}

    if sells:
        results |= executor.execute(client, sells, prices)
        if not client.dry_run:
            record_orders(trade_date, sells, results)

    # --- buys, replanned against the cash the sells actually raised ----
    buys = [o for o in orders if o.side == "BUY"]
    if buys:
        positions, cash = snapshot(client)
        prices = current_prices(client, set(signal.weights) | set(positions))
        buys = [o for o in rebalance.plan(signal.weights, positions,
                                          prices, cash) if o.side == "BUY"]

        log.info("\nrevised buy plan:\n%s", rebalance.format_plan(buys))
        if buys and confirm("Proceed with buys?"):
            results |= executor.execute(client, buys, prices)
            record_orders(trade_date, buys, results)

    # --- verify -------------------------------------------------------
    positions, cash = snapshot(client)
    total = cash + sum(p.value for p in positions.values())
    log.info("final: %s KRW cash, %d positions, %s total",
             f"{cash:,.0f}", len(positions), f"{total:,.0f}")

    failed = [s for s, r in results.items() if not r.get("filled")]
    if failed:
        log.warning("unfilled: %s", failed)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())