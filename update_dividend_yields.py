"""Warm the dividend-event cache and print each symbol's trailing yield.

    python update_dividend_yields.py

Not read-only: syncs quant/dividends.py's cache (writes dividend_events/
dividend_fetch_state via the live KIS payout endpoint) for every UNIVERSE +
WATCH_ONLY + HELD_ETFS symbol, then prints a report. daily.py and
rebalance_run.py already do this sync inline before every strategy run, so
this script is not required for correctness - it exists to trigger and
eyeball a backfill on demand (e.g. right after deploying a change here,
without waiting for the next cron tick) rather than waiting to see it
happen as a side effect of the next live run.
"""

import sys

from quant import accounts, config, dividends, kis_client, storage


def main() -> int:
    storage.init()

    cfg = accounts.resolve("kis-isa")
    client = cfg["client"]()

    symbols = sorted(set(config.UNIVERSE) | set(config.WATCH_ONLY)
                     | set(config.HELD_ETFS))

    with storage.connect() as conn:
        dividends.sync_all(conn, client, symbols)
        events = dividends.load_all(conn, symbols)

    prices = kis_client.batch_price(client, set(symbols))

    print(f"{'symbol':<8}{'name':<26}{'trailing 12mo':>15}{'events':>9}")
    for sym in symbols:
        name = (config.UNIVERSE.get(sym) or config.WATCH_ONLY.get(sym)
                or config.HELD_ETFS.get(sym, sym))
        price = prices.get(sym)
        if price is None or price <= 0:
            print(f"{sym:<8}{name:<26}{'no price':>15}")
            continue

        yield_ = kis_client.dividend_yield(client, sym, price)
        print(f"{sym:<8}{name:<26}{yield_:>14.2%}{len(events[sym]):>9}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
