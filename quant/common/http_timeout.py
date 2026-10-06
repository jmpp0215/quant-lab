"""Default network timeouts for libraries that never pass one.

FinanceDataReader calls requests.get(...) and, for KRX listings, pandas'
urllib.request.urlopen(...) without a timeout. An unanswered request then
blocks forever - refresh_prices.py hung for 22 hours on 2026-10-05.

socket.setdefaulttimeout() alone is not enough: requests hands timeout=None
explicitly down to urllib3, which means "no timeout", so the socket default
never applies (verified 2026-10-06: a local server that accepts and never
answers held requests.get past 30s under a 2s socket default). urllib's
urlopen does use the socket default. install() covers both.

Code that passes its own timeout (kis_client, toss_client, dart.py) is
unaffected - only a missing/None timeout is filled in.
"""
import socket

import requests

DEFAULT_TIMEOUT = 30.0


def install(seconds: float = DEFAULT_TIMEOUT) -> None:
    socket.setdefaulttimeout(seconds)

    current = requests.Session.request
    original = getattr(current, "_without_default_timeout", current)

    def request(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = seconds
        return original(self, method, url, **kwargs)

    request._without_default_timeout = original
    requests.Session.request = request
