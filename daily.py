"""Daily signal run. Executed once per trading day by cron.

Fetches candles, evaluates the strategy, and records the ranking. Reruns
on the same trading date overwrite: the strategy only acts on completed
daily candles, so intraday recalculation carries no new information.

Placing orders is deliberately not part of this yet - the observation
period runs until the first scheduled rebalance.
"""

import logging
import sys
from datetime import datetime
import indicators
import candles
import config
import logging_config
import market
import storage
import strategy
import rebalance
from decimal import Decimal
from toss_client import TossClient, TossApiError

log = logging.getLogger("daily")


def record(signal: strategy.Signal, trade_date: str, session: str | None,
           candles_by_symbol: dict[str, list[dict]],
           positions: dict[str, rebalance.Position],
           cash: Decimal, skip_portfolio: bool = False) -> None:
    now = datetime.now().astimezone().isoformat()
    rows = [
        {
            "symbol": score.symbol,
            "rank": rank,
            "momentum": score.momentum,
            "selected": score.symbol in signal.weights,
            "weight": signal.weights.get(score.symbol),
        }
        for rank, score in enumerate(signal.scores, 1)
    ]

    ind_rows = [
        (symbol, name, value)
        for symbol in config.all_symbols()
        for name, value in indicators.compute_all(
            candles_by_symbol.get(symbol, []),
            config.DIVIDEND_YIELD.get(symbol, Decimal("0")),
        ).items()
    ]

    total = cash + sum(p.value for p in positions.values())
    pos_rows = [
        {"symbol": p.symbol, "name": p.name,
         "qty": p.quantity, "price": str(p.last_price)}
        for p in positions.values()
    ]
    variant_rows = [
        (v.name, symbol, weight)
        for v in strategy.variants(candles_by_symbol, signal)
        for symbol, weight in v.weights.items()
    ]

    with storage.connect() as conn:
        storage.save_run(conn, trade_date, now, session, ok=True)
        storage.save_scores(conn, trade_date, rows)
        storage.save_indicators(conn, trade_date, ind_rows)
        storage.save_variants(conn, trade_date, variant_rows)

        # Holdings can only be observed now, never reconstructed, so a
        # backfilled date must not be given today's balance.
        if not skip_portfolio:
            storage.save_portfolio(conn, trade_date, "toss-bot", "KRW",
                                   total, cash, pos_rows)
    
    log.info("recorded %d scores, %d indicators, %d variant weights",
             len(rows), len(ind_rows), len(variant_rows))

def main() -> int:
    logging_config.setup()
    log.info("daily run start")

    storage.init()
    # Recompute a past date from cached candles. The signal is a pure
    # function of the candles up to that date, so a missed run can be
    # filled in later without losing anything.
    target = None
    if "--date" in sys.argv:
        target = sys.argv[sys.argv.index("--date") + 1]
        log.info("backfilling %s", target)

    try:
        client = TossClient()

        if target is None:
            calendar = client.market_calendar("KR")
            if not market.is_business_day(calendar):
                log.info("market closed today; nothing to record")
                return 0
            session = market.current_session(calendar)
            session_closed = session in (None, "afterMarket")
        else:
            # A past date's candles are complete by definition.
            session = None
            session_closed = True
        
        # Once the regular session has closed, today's candle is final and
        # should drive the signal; before that it is still moving.
        data = {
            sym: candles.get(client, sym, days=config.HISTORY_DAYS,
                             include_today=session_closed)
            for sym in config.all_symbols()
        }
        if target is not None:
            data = {
                sym: [c for c in cs if c["timestamp"][:10] <= target]
                for sym, cs in data.items()
            }

        # The trade date is the newest completed candle, not today - during
        # market hours these differ, and the signal belongs to the former.
        reference = data[next(iter(config.UNIVERSE))]
        if not reference:
            log.error("no candles at or before %s", target)
            return 1
        trade_date = reference[0]["timestamp"][:10]
        log.info("trade date: %s", trade_date)

        signal = strategy.evaluate(data)
        log.info("\n%s", strategy.format_signal(signal))
        if target is None:
            positions = rebalance.parse_positions(client.holdings())
            cash = Decimal(
                client.buying_power("KRW")["result"]["cashBuyingPower"])
        else:
            positions, cash = {}, Decimal("0")

        record(signal, trade_date, session, data, positions, cash,
               skip_portfolio=target is not None)

    except TossApiError as e:
        log.error("api error: %s", e)
        return 1
    except Exception:
        log.exception("daily run failed")
        return 1

    log.info("daily run done")
    return 0


if __name__ == "__main__":
    sys.exit(main())