"""Tests for execution safety checks."""

from decimal import Decimal

import pytest

import executor
from executor import ExecutionError, Touch
from rebalance import Order


def order(side: str, qty: int = 10) -> Order:
    return Order(symbol="102110", name="TIGER 200", side=side,
                 quantity=qty, limit_price=Decimal("109620"))


class TestLimitPrice:
    def test_buy_takes_the_ask(self):
        touch = Touch(bid=Decimal("109615"), ask=Decimal("109620"),
                      bid_volume=300, ask_volume=4222)
        price = executor.limit_price_for(order("BUY"), touch,
                                         Decimal("109615"))
        assert price == Decimal("109620")

    def test_sell_takes_the_bid(self):
        touch = Touch(bid=Decimal("109615"), ask=Decimal("109620"),
                      bid_volume=300, ask_volume=4222)
        price = executor.limit_price_for(order("SELL"), touch,
                                         Decimal("109620"))
        assert price == Decimal("109615")

    def test_refuses_empty_book(self):
        touch = Touch(bid=None, ask=None, bid_volume=0, ask_volume=0)
        with pytest.raises(ExecutionError, match="no ask"):
            executor.limit_price_for(order("BUY"), touch, Decimal("109615"))

    def test_refuses_touch_far_from_last(self):
        # The scenario this whole design exists to prevent: a thin book
        # quoting far from the last trade.
        touch = Touch(bid=Decimal("60000"), ask=Decimal("60000"),
                      bid_volume=1, ask_volume=1)
        with pytest.raises(ExecutionError, match="abnormal book"):
            executor.limit_price_for(order("SELL"), touch, Decimal("109615"))

    def test_allows_deviation_within_limit(self):
        touch = Touch(bid=Decimal("108000"), ask=Decimal("108005"),
                      bid_volume=100, ask_volume=100)
        price = executor.limit_price_for(order("BUY"), touch,
                                         Decimal("109615"))
        assert price == Decimal("108005")


class TestAuctionGuard:
    def test_blocks_during_closing_auction(self):
        assert executor.auction_imminent("15:20")
        assert executor.auction_imminent("15:25")

    def test_allows_before_auction(self):
        assert not executor.auction_imminent("14:59")
        assert not executor.auction_imminent("10:00")