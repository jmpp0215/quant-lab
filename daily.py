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

import candles
import config
import logging_config
import market
import storage
import strategy
from toss_client import TossClient, TossApiError

log = logging.getLogger("daily")


def record(signal: strategy.Signal, trade_date: str,
           session: str | None) -> None:
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

    with storage.connect() as conn:
        storage.save_run(conn, trade_date, now, session, ok=True)
        storage.save_scores(conn, trade_date, rows)

    log.info("recorded %d scores for %s", len(rows), trade_date)


def main() -> int:
    logging_config.setup()
    log.info("daily run start")

    storage.init()

    try:
        client = TossClient()

        session = market.current_session(client.market_calendar("KR"))
        log.info("KR session: %s", session)

        data = {
            sym: candles.get(client, sym, days=config.HISTORY_DAYS)
            for sym in config.all_symbols()
        }

        # The trade date is the newest completed candle, not today - during
        # market hours these differ, and the signal belongs to the former.
        reference = data[next(iter(config.UNIVERSE))]
        trade_date = reference[0]["timestamp"][:10]
        log.info("trade date: %s", trade_date)

        signal = strategy.evaluate(data)
        log.info("\n%s", strategy.format_signal(signal))
        record(signal, trade_date, session)

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