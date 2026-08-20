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

DEFAULT_ACCOUNT = "toss-bot"

_kis_main_client = lambda: KisClient("main")

ACCOUNTS = {
    "toss-bot": {"client": TossClient, "snapshot": toss_client.snapshot,
                 "price": toss_client.batch_price, "strategy": False},
    "kis-main": {"client": _kis_main_client, "snapshot": kis_client.snapshot,
                 "price": kis_client.batch_price, "strategy": False},
    # Domestic and overseas balance are separate endpoints on the same KIS
    # account - shares _kis_main_client with "kis-main" rather than
    # authenticating twice for one account.
    "kis-main-overseas": {"client": _kis_main_client,
                          "snapshot": kis_client.snapshot_overseas,
                          "price": kis_client.batch_price, "strategy": False},
    "kis-isa": {"client": lambda: KisClient("isa"), "snapshot": kis_client.snapshot,
                "price": kis_client.batch_price, "strategy": True},
}


def resolve(account: str) -> dict:
    """{"client", "snapshot", "price", "strategy"} for one account name."""
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
