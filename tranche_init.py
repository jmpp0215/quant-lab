"""Assign the current account holdings to tranches.

No trading happens: this only writes down which sleeve owns which shares.
Run once, when starting staggered rebalancing on an existing position.
"""

import logging
import sys
from datetime import datetime

import config
import logging_config
import rebalance
import storage
import tranche
from toss_client import TossClient

ACCOUNT = "toss-bot"

log = logging.getLogger("tranche-init")


def main() -> int:
    logging_config.setup()
    storage.init()

    client = TossClient()
    positions = rebalance.parse_positions(client.holdings())
    actual = {p.symbol: p.quantity for p in positions.values()}

    if not actual:
        log.error("no holdings to split")
        return 1

    with storage.connect() as conn:
        existing = storage.load_all_tranche_holdings(conn, ACCOUNT)

    if existing and "--force" not in sys.argv:
        log.error("tranche books already exist; use --force to overwrite")
        for t, holdings in sorted(existing.items()):
            log.error("  tranche %d: %s", t, holdings)
        return 1

    split = tranche.initial_split(actual)

    print("\ncurrent account:")
    for symbol, quantity in sorted(actual.items()):
        print(f"  {config.UNIVERSE.get(symbol, symbol):<24} {quantity:>5}")

    print("\nproposed split:")
    for t in config.TRANCHES:
        holdings = split[t]
        print(f"  tranche {t}:")
        for symbol, quantity in sorted(holdings.items()):
            print(f"    {config.UNIVERSE.get(symbol, symbol):<22} {quantity:>5}")

    drift = tranche.reconcile(split, actual)
    if drift:
        log.error("split does not account for every share: %s", drift)
        return 1

    if input("\nWrite this split? [yes/no]: ").strip().lower() != "yes":
        log.info("aborted")
        return 0

    now = datetime.now().astimezone().isoformat()
    with storage.connect() as conn:
        for t, holdings in split.items():
            storage.save_tranche_holdings(conn, t, ACCOUNT, holdings, now)

    log.info("tranche books written")
    return 0


if __name__ == "__main__":
    sys.exit(main())