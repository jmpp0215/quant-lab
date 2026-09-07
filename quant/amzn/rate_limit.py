"""Minimal rate limiter for KIS Open API calls (20 req/sec cap).

No shared rate-limit utility exists elsewhere in this codebase - kis_client's
existing overseas/domestic methods only ever get called a handful of times
per run by daily.py/rebalance_run.py, so per-second bursts never came up.
The AMZN news collector can page through many headlines in one run, so it
needs an explicit throttle.
"""
import time

# KIS's documented cap; stay under it rather than exactly at it.
MAX_CALLS_PER_SECOND = 15


class RateLimiter:
    def __init__(self, max_per_second: int = MAX_CALLS_PER_SECOND) -> None:
        self._min_interval = 1.0 / max_per_second
        self._last_call: float = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        remaining = self._min_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_call = time.monotonic()
