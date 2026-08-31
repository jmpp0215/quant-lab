"""Account registry: which broker/credentials back each account name.

Client factories are lazy - called only when a script actually needs that
account - so a missing credential for one account doesn't stop other
accounts' env vars from ever being read. kis-main and kis-main-overseas
share one factory object (see daily.py's identity-keyed client cache) so
they authenticate once, not twice, for the same KIS account.
"""

from quant import kis_client, toss_client
from quant.kis_client import KisClient
from quant.toss_client import TossClient

DEFAULT_ACCOUNT = "kis-isa"

_kis_main_client = lambda: KisClient("main")

# "broker" is the module implementing quant/broker.py's execution
# interface for that account - executor is handed it and never touches a
# broker's response shape itself. None means orders cannot be placed for
# this account at all.
ACCOUNTS = {
    "toss-bot": {"client": TossClient, "snapshot": toss_client.snapshot,
                 "price": toss_client.batch_price, "broker": toss_client,
                 "buying_power": toss_client.available_cash,
                 "strategy": False, "tradable": True},
    # Observed (snapshotted daily) but never traded by this strategy -
    # predates it and isn't tranche_init'd, so rebalance_run.py must
    # refuse it rather than relying on that as an accident of empty books.
    "kis-main": {"client": _kis_main_client, "snapshot": kis_client.snapshot,
                 "price": kis_client.batch_price, "broker": kis_client,
                 "buying_power": kis_client.available_cash,
                 "strategy": False, "tradable": False},
    # Domestic and overseas balance are separate endpoints on the same KIS
    # account - shares _kis_main_client with "kis-main" rather than
    # authenticating twice for one account. No broker: overseas ordering
    # is a different KIS endpoint family that kis_client does not
    # implement, so this account can be snapshotted but never traded.
    "kis-main-overseas": {"client": _kis_main_client,
                          "snapshot": kis_client.snapshot_overseas,
                          "price": kis_client.batch_price, "broker": None,
                          "buying_power": None,
                          "strategy": False, "tradable": False},
    "kis-isa": {"client": lambda: KisClient("isa"), "snapshot": kis_client.snapshot,
                "price": kis_client.batch_price, "broker": kis_client,
                "buying_power": kis_client.available_cash,
                "strategy": True, "tradable": True},
}


def resolve(account: str) -> dict:
    """{"client", "snapshot", "price", "broker", "buying_power", "strategy",
    "tradable"} for one account.

    "strategy" and "tradable" answer different questions: "strategy" picks
    the one account daily.py records the shared momentum signal against
    (arbitrary - the signal doesn't depend on the account), while
    "tradable" is which accounts rebalance_run.py is allowed to send
    orders for. toss-bot is "strategy": False (daily.py doesn't need to
    record the signal twice) but "tradable": True (it's the sandbox
    account for ad hoc live trials) - the two are not interchangeable.
    """
    try:
        return ACCOUNTS[account]
    except KeyError:
        raise ValueError(
            f"unknown account {account!r}; choices: {sorted(ACCOUNTS)}"
        ) from None


def get_client(account: str):
    """Instantiate the client for one account name."""
    return resolve(account)["client"]()


def extract_account(argv: list[str], default: str = DEFAULT_ACCOUNT
                    ) -> tuple[str, list[str]]:
    """Pull `--account NAME` out of argv, wherever it appears.

    Returns (account, remaining argv) so callers can keep parsing their own
    positional args/flags without --account shifting them around.
    """
    argv = list(argv)
    if "--account" in argv:
        i = argv.index("--account")
        account = argv[i + 1]
        del argv[i:i + 2]
        return account, argv
    return default, argv
