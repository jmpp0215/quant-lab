"""Tests for market session and tick size logic."""

from decimal import Decimal

import pytest

import market


class TestTickSize:
    @pytest.mark.parametrize("price,expected", [
        ("500", "1"),
        ("1500", "1"),
        ("3000", "5"),
        ("7000", "10"),
        ("15000", "10"),
        ("30000", "50"),
        ("70000", "100"),
        ("150000", "100"),
        ("241000", "500"),
        ("600000", "1000"),
    ])
    def test_returns_correct_tick_for_price_band(self, price, expected):
        assert market.kr_tick_size(price) == Decimal(expected)

    def test_boundary_belongs_to_upper_band(self):
        # 2000 is the start of the 5-won band, not the end of the 1-won one.
        assert market.kr_tick_size("1999") == Decimal("1")
        assert market.kr_tick_size("2000") == Decimal("5")


class TestPriceValidation:
    def test_accepts_price_on_tick(self):
        assert market.is_valid_kr_price("241000")
        assert market.is_valid_kr_price("15760")

    def test_rejects_price_off_tick(self):
        assert not market.is_valid_kr_price("241001")
        assert not market.is_valid_kr_price("15765")

    def test_accepts_decimal_input(self):
        assert market.is_valid_kr_price(Decimal("241000"))


class TestRoundToTick:
    def test_rounds_down_to_nearest_tick(self):
        assert market.round_to_tick(Decimal("241037")) == Decimal("241000")
        assert market.round_to_tick(Decimal("15767")) == Decimal("15760")

    def test_leaves_valid_price_unchanged(self):
        assert market.round_to_tick(Decimal("241000")) == Decimal("241000")

    def test_result_is_always_valid(self):
        for raw in ("1537", "48037", "163537", "241037", "600037"):
            snapped = market.round_to_tick(Decimal(raw))
            assert market.is_valid_kr_price(snapped)