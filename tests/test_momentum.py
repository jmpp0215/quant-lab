"""Tests for momentum calculation."""

from decimal import Decimal

from quant import momentum


def candles(prices: list[tuple[str, str]]) -> list[dict]:
    """Build newest-first candles from (date, close) pairs."""
    return [
        {"timestamp": f"{d}T00:00:00.000+09:00", "closePrice": p}
        for d, p in prices
    ]


class TestPriceReturn:
    def test_computes_return_over_window(self):
        cs = candles([("2026-08-13", "110"), ("2025-08-13", "100")])
        assert momentum.price_return(cs, 12) == Decimal("0.1")

    def test_returns_none_when_history_too_short(self):
        cs = candles([("2026-08-13", "110"), ("2026-06-13", "100")])
        assert momentum.price_return(cs, 12) is None

    def test_skip_months_excludes_recent_window(self):
        # 12-1: measure to a month ago, not to today. The recent drop
        # from 120 to 110 must not be part of the return.
        cs = candles([
            ("2026-08-13", "110"),
            ("2026-07-13", "120"),
            ("2025-08-13", "100"),
        ])
        assert momentum.price_return(cs, 12, skip_months=1) == Decimal("0.2")

    def test_uses_last_trading_day_before_target(self):
        # No candle exactly 12 months back; the closest earlier one is used
        # rather than failing or silently picking a later date.
        cs = candles([("2026-08-13", "110"), ("2025-08-08", "100")])
        assert momentum.price_return(cs, 12) == Decimal("0.1")

    def test_returns_none_on_zero_past_price(self):
        cs = candles([("2026-08-13", "110"), ("2025-08-13", "0")])
        assert momentum.price_return(cs, 12) is None

    def test_returns_none_on_empty_input(self):
        assert momentum.price_return([], 12) is None


def events(rows: list[tuple[str, str]]) -> list[dict]:
    """Build dividend events from (record_date, amount) pairs."""
    return [{"record_date": d, "amount": Decimal(a)} for d, a in rows]


class TestTrailingYield:
    def test_sums_events_inside_the_window(self):
        cs = candles([("2026-08-13", "100")])
        es = events([("2026-05-01", "2"), ("2026-02-01", "3")])
        # (2 + 3) / 100, both within the trailing 12 months.
        assert momentum.trailing_yield(cs, es, months=12) == Decimal("0.05")

    def test_excludes_events_before_the_cutoff(self):
        cs = candles([("2026-08-13", "100")])
        es = events([("2026-05-01", "2"), ("2024-01-01", "50")])
        assert momentum.trailing_yield(cs, es, months=12) == Decimal("0.02")

    def test_excludes_events_after_the_anchor(self):
        # Defense in depth: a caller should already have sliced these out,
        # but a future event must not count anyway.
        cs = candles([("2026-08-13", "100")])
        es = events([("2026-05-01", "2"), ("2026-09-01", "10")])
        assert momentum.trailing_yield(cs, es, months=12) == Decimal("0.02")

    def test_empty_events_is_zero(self):
        cs = candles([("2026-08-13", "100")])
        assert momentum.trailing_yield(cs, [], months=12) == Decimal("0")

    def test_empty_candles_is_zero(self):
        es = events([("2026-05-01", "2")])
        assert momentum.trailing_yield([], es, months=12) == Decimal("0")

    def test_zero_price_is_zero(self):
        cs = candles([("2026-08-13", "0")])
        es = events([("2026-05-01", "2")])
        assert momentum.trailing_yield(cs, es, months=12) == Decimal("0")