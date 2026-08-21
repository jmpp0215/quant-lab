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
from decimal import Decimal

from quant import (
    accounts,
    candles,
    config,
    indicators,
    logging_config,
    market,
    storage,
    strategy,
)
from quant.kis_client import KisApiError
from quant.toss_client import TossApiError, TossClient

log = logging.getLogger("daily")


def record_strategy(signal: strategy.Signal, trade_date: str,
                    session: str | None,
                    candles_by_symbol: dict[str, list[dict]]) -> None:
    """Record the shared dual-momentum signal - not account state."""
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
        # Candle/calendar data is shared market data, not account state -
        # Toss happens to be the source for it regardless of which account
        # the ISA strategy signal ends up being recorded against.
        market_client = TossClient()

        if target is None:
            calendar = market_client.market_calendar("KR")
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
            sym: candles.get(market_client, sym, days=config.HISTORY_DAYS,
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
    except Exception:
        log.exception("daily run failed")
        return 1

    overall_ok = True
    # Seeded with the market client so the toss-bot account reuses it
    # instead of authenticating a second time: Toss keeps only one active
    # token per credential set, and a second one silently invalidates the
    # first. Today the candle work is all finished before this loop, so a
    # second token would do no harm - but that is ordering luck, not a
    # guarantee worth resting on.
    client_cache: dict = {TossClient: market_client}
    for account_name, cfg in accounts.ACCOUNTS.items():
        try:
            factory = cfg["client"]
            if factory not in client_cache:
                client_cache[factory] = factory()
            client = client_cache[factory]

            if cfg["strategy"]:
                signal = strategy.evaluate(data)
                log.info("\n%s", strategy.format_signal(signal))
                record_strategy(signal, trade_date, session, data)

            if target is None:
                # Holdings can only be observed now, never reconstructed,
                # so a backfilled date must not be given today's balance.
                snap = cfg["snapshot"](client)
                if snap.account != account_name:
                    log.warning("%s: snapshot labeled itself %s",
                               account_name, snap.account)
                with storage.connect() as conn:
                    storage.save_portfolio(conn, trade_date, account_name,
                                           snap.currency, snap.total,
                                           snap.cash, snap.positions)
        except (TossApiError, KisApiError) as e:
            log.error("%s: api error: %s", account_name, e)
            overall_ok = False
        except Exception:
            log.exception("%s: account run failed", account_name)
            overall_ok = False

    log.info("daily run done")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())