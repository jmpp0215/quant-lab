"""Tests for order planning.

This module decides how much real money moves, so the arithmetic is
covered case by case rather than trusted to a single happy path.
"""

from decimal import Decimal

import pytest

import rebalance
from rebalance import Position


def pos(symbol: str, qty: int, price: str) -> Position:
    return Position(symbol=symbol, name=symbol, quantity=qty,
                    last_price=Decimal(price))


THIRD = Decimal("1") / 3


class TestTotalAssets:
    def test_buys_use_holdings_plus_cash(self):
        # 1,000,000 cash + 1,000,000 held = 2,000,000 total.
        # A full 100% weight at 10,000/share should buy 200 shares,
        # not 100 - the cash alone would only cover half.
        orders = rebalance.plan(
            target_weights={"AAA": Decimal("1")},
            positions={"BBB": pos("BBB", 100, "10000")},
            prices={"AAA": Decimal("10000"), "BBB": Decimal("10000")},
            cash=Decimal("1000000"),
        )
        buy = next(o for o in orders if o.symbol == "AAA")
        assert buy.quantity == 200


class TestQuantityRounding:
    def test_rounds_down_to_whole_shares(self):
        # 1,000,000 / 3 = 333,333 target; at 100,000/share that is 3 shares,
        # leaving the remainder in cash rather than overshooting to 4.
        orders = rebalance.plan(
            target_weights={"AAA": THIRD},
            positions={},
            prices={"AAA": Decimal("100000")},
            cash=Decimal("1000000"),
        )
        assert orders[0].quantity == 3

    def test_skips_symbol_when_one_share_is_unaffordable(self):
        # Target is 333,333 but a share costs 1,000,000.
        orders = rebalance.plan(
            target_weights={"AAA": THIRD},
            positions={},
            prices={"AAA": Decimal("1000000")},
            cash=Decimal("1000000"),
        )
        assert orders == []


class TestDelta:
    def test_no_order_when_already_at_target(self):
        orders = rebalance.plan(
            target_weights={"AAA": Decimal("1")},
            positions={"AAA": pos("AAA", 100, "10000")},
            prices={"AAA": Decimal("10000")},
            cash=Decimal("0"),
        )
        assert orders == []

    def test_sells_entire_position_absent_from_target(self):
        orders = rebalance.plan(
            target_weights={},
            positions={"AAA": pos("AAA", 100, "10000")},
            prices={"AAA": Decimal("10000")},
            cash=Decimal("0"),
        )
        assert len(orders) == 1
        assert orders[0].side == "SELL"
        assert orders[0].quantity == 100

    def test_trades_only_the_difference(self):
        # Total 1,000,000 at 100% weight = 10 shares wanted, 4 already held.
        orders = rebalance.plan(
            target_weights={"AAA": Decimal("1")},
            positions={"AAA": pos("AAA", 4, "100000")},
            prices={"AAA": Decimal("100000")},
            cash=Decimal("600000"),
        )
        assert orders[0].side == "BUY"
        assert orders[0].quantity == 6


class TestMinimumOrder:
    def test_skips_trade_below_minimum(self):
        # A one-share adjustment worth less than the threshold costs more
        # in fees and spread than the tracking error it corrects.
        orders = rebalance.plan(
            target_weights={"AAA": Decimal("1")},
            positions={"AAA": pos("AAA", 100, "1000")},
            prices={"AAA": Decimal("1000")},
            cash=Decimal("1000"),
        )
        assert orders == []


class TestOrdering:
    def test_sells_come_before_buys(self):
        # Cash from the sell is needed to fund the buy, so the caller must
        # be able to send them in list order.
        orders = rebalance.plan(
            target_weights={"BBB": Decimal("1")},
            positions={"AAA": pos("AAA", 100, "10000")},
            prices={"AAA": Decimal("10000"), "BBB": Decimal("10000")},
            cash=Decimal("0"),
        )
        sides = [o.side for o in orders]
        assert sides == ["SELL", "BUY"]


class TestPriceHandling:
    def test_snaps_limit_price_to_tick(self):
        orders = rebalance.plan(
            target_weights={"005930": Decimal("1")},
            positions={},
            prices={"005930": Decimal("241037")},
            cash=Decimal("1000000"),
        )
        assert orders[0].limit_price == Decimal("241000")

    def test_skips_symbol_with_no_price(self):
        orders = rebalance.plan(
            target_weights={"AAA": Decimal("1")},
            positions={},
            prices={},
            cash=Decimal("1000000"),
        )
        assert orders == []