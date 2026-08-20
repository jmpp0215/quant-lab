"""Assign the current account holdings to tranches.

    python tranche_init.py                    toss-bot (default)
    python tranche_init.py --account kis-isa
    python tranche_init.py --account kis-isa --force

No trading happens: this only writes down which sleeve owns which shares.
Run once, when starting staggered rebalancing on an existing position.
"""

import logging
import sys
from datetime import datetime

from quant import accounts, config, logging_config, storage, tranche

log = logging.getLogger("tranche-init")


def main() -> int:
    logging_config.setup()
    storage.init()

    account, rest = accounts.extract_account(sys.argv[1:])
    cfg = accounts.resolve(account)
    client = cfg["client"]()
    snap = cfg["snapshot"](client)
    names = {p["symbol"]: p["name"] for p in snap.positions}
    actual = {p["symbol"]: p["qty"] for p in snap.positions}

    if not actual:
        log.error("no holdings to split")
        return 1

    with storage.connect() as conn:
        existing = storage.load_all_tranche_holdings(conn, account)

    if existing and "--force" not in rest:
        log.error("tranche books already exist; use --force to overwrite")
        for t, holdings in sorted(existing.items()):
            log.error("  tranche %d: %s", t, holdings)
        return 1

    split = tranche.initial_split(actual)

    print("\ncurrent account:")
    for symbol, quantity in sorted(actual.items()):
        print(f"  {names.get(symbol, symbol):<24} {quantity:>5}")

    print("\nproposed split:")
    for t in config.TRANCHES:
        holdings = split[t]
        print(f"  tranche {t}:")
        for symbol, quantity in sorted(holdings.items()):
            print(f"    {names.get(symbol, symbol):<22} {quantity:>5}")

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
            storage.save_tranche_holdings(conn, t, account, holdings, now)

    log.info("tranche books written")
    return 0


if __name__ == "__main__":
    sys.exit(main())