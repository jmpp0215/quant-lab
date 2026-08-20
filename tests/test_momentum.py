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


class TestTotalReturn:
    def test_adds_dividend_prorated_to_holding_window(self):
        cs = candles([("2026-08-13", "110"), ("2025-08-13", "100")])
        # 10% price + 12/12 of a 5% annual yield.
        result = momentum.total_return(cs, 12, 0, Decimal("0.05"))
        assert result == Decimal("0.15")

    def test_prorates_dividend_over_skip_adjusted_window(self):
        cs = candles([
            ("2026-08-13", "110"),
            ("2026-07-13", "110"),
            ("2025-08-13", "100"),
        ])
        # 12-1 holds for 11 months, so only 11/12 of the yield applies.
        result = momentum.total_return(cs, 12, 1, Decimal("0.12"))
        assert result == Decimal("0.21")

    def test_zero_yield_matches_price_return(self):
        cs = candles([("2026-08-13", "110"), ("2025-08-13", "100")])
        assert momentum.total_return(cs, 12) == momentum.price_return(cs, 12)