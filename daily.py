"""Daily signal run. Executed once per trading day by cron.

Fetches candles, evaluates the strategy, and appends the ranking to a
JSONL log. Placing orders is deliberately not part of this yet: the
observation period runs until the first scheduled rebalance.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from decimal import Decimal
import candles
import config
import logging_config
import market
import strategy
from toss_client import TossClient, TossApiError

SIGNAL_LOG = Path(__file__).parent / "data" / "signals.jsonl"

log = logging.getLogger("daily")

LAST_RUN = Path(__file__).parent / "data" / "last_run.txt"


def already_ran_today(today: str) -> bool:
    """Guard against double runs - a duplicate rebalance would double the
    position size, since unfilled orders leave holdings unchanged.
    """
    if not LAST_RUN.exists():
        return False
    return LAST_RUN.read_text().strip() == today


def mark_ran(today: str) -> None:
    LAST_RUN.parent.mkdir(parents=True, exist_ok=True)
    LAST_RUN.write_text(today)


def record(signal: strategy.Signal, session: str | None) -> None:
    """Append one line of JSON so the history stays easy to analyse later."""
    SIGNAL_LOG.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "session": session,
        "weights": {s: str(w) for s, w in signal.weights.items()},
        "cash_weight": str(signal.cash_weight),
        "ranking": [
            {
                "rank": i,
                "symbol": s.symbol,
                "name": s.name,
                "momentum": str(s.momentum.quantize(Decimal("0.0001")))
                            if s.ranked else None,
                "selected": s.symbol in signal.weights,
            }
            for i, s in enumerate(signal.scores, 1)
        ],
    }

    with SIGNAL_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    log.info("signal recorded to %s", SIGNAL_LOG)


def main() -> int:
    logging_config.setup()

    today = datetime.now().astimezone().date().isoformat()

    if already_ran_today(today) and "--force" not in sys.argv:
        log.info("already ran today (%s), skipping. use --force to override",
                 today)
        return 0

    log.info("daily run start")

    try:
        client = TossClient()

        calendar = client.market_calendar("KR")
        session = market.current_session(calendar)
        log.info("KR session: %s", session)

        data = {
            sym: candles.get(client, sym, days=config.HISTORY_DAYS)
            for sym in config.all_symbols()
        }

        signal = strategy.evaluate(data)
        log.info("\n%s", strategy.format_signal(signal))
        record(signal, session)

    except TossApiError as e:
        log.error("api error: %s", e)
        return 1
    except Exception:
        log.exception("daily run failed")
        return 1

    mark_ran(today)
    log.info("daily run done")
    return 0


if __name__ == "__main__":
    sys.exit(main())