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
    def test_buy_prices_through_the_ask(self):
        touch = Touch(bid=Decimal("109615"), ask=Decimal("109620"),
                      bid_volume=300, ask_volume=4222)
        price = executor.limit_price_for(order("BUY"), touch,
                                         Decimal("109615"))
        # One tick above the ask: enough to survive the book moving between
        # reading it and the order arriving, without paying for depth the
        # retry loop can pick up instead.
        assert price == Decimal("109625")

    def test_sell_prices_through_the_bid(self):
        touch = Touch(bid=Decimal("109615"), ask=Decimal("109620"),
                      bid_volume=300, ask_volume=4222)
        price = executor.limit_price_for(order("SELL"), touch,
                                         Decimal("109620"))
        assert price == Decimal("109610")

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
        assert price == Decimal("108010")


class TestAuctionGuard:
    def test_blocks_during_closing_auction(self):
        assert executor.auction_imminent("15:20")
        assert executor.auction_imminent("15:25")

    def test_allows_before_auction(self):
        assert not executor.auction_imminent("14:59")
        assert not executor.auction_imminent("10:00")


class FakeClient:
    """Minimal stand-in for TossClient.

    Records every call so the test can assert on the sequence, and lets
    each scenario script when a fill happens.
    """

    def __init__(self, fill_on_attempt: int | None = 1,
                 touch: Touch | None = None):
        self.fill_on_attempt = fill_on_attempt
        self.touch = touch or Touch(
            bid=Decimal("109615"), ask=Decimal("109620"),
            bid_volume=1000, ask_volume=1000,
        )
        self.dry_run = False
        self.placed: list[dict] = []
        self.cancelled: list[str] = []
        self._open: list[dict] = []

    # --- endpoints used by executor ---------------------------------

    def get(self, path, params=None, need_account=False):
        if path == "/api/v1/orderbook":
            return {"result": {
                "asks": ([{"price": str(self.touch.ask),
                           "volume": str(self.touch.ask_volume)}]
                         if self.touch.ask else []),
                "bids": ([{"price": str(self.touch.bid),
                           "volume": str(self.touch.bid_volume)}]
                         if self.touch.bid else []),
            }}
        raise AssertionError(f"unexpected GET {path}")

    def create_order(self, symbol, side, order_type, quantity, price=None):
        attempt = len(self.placed) + 1
        order_id = f"order-{attempt}"
        self.placed.append({"symbol": symbol, "side": side,
                            "quantity": quantity, "price": price,
                            "order_id": order_id})

        # An order rests in the open book unless this attempt is the one
        # scripted to fill.
        if attempt != self.fill_on_attempt:
            self._open.append({
                "orderId": order_id,
                "quantity": str(quantity),
                "execution": {"filledQuantity": "0"},
            })
        return {"result": {"orderId": order_id}}

    def list_orders(self, status="OPEN"):
        if status == "OPEN":
            return {"result": {"orders": list(self._open)}}
        return {"result": {"orders": []}}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        self._open = [o for o in self._open if o["orderId"] != order_id]
        return {"result": {}}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Collapse the polling waits so tests run instantly."""
    monkeypatch.setattr(executor.time, "sleep", lambda _: None)
    monkeypatch.setattr(executor, "FILL_TIMEOUT", 1)


class TestExecuteRetry:
    def test_fills_on_first_attempt(self):
        client = FakeClient(fill_on_attempt=1)
        results = executor.execute(client, [order("BUY")],
                                   {"102110": Decimal("109615")})

        assert results["102110"]["filled"]
        assert len(client.placed) == 1
        assert client.cancelled == []

    def test_cancels_and_reprices_when_unfilled(self):
        # The first order rests unfilled; the executor must cancel it
        # before placing another, or the account ends up with both.
        client = FakeClient(fill_on_attempt=2)
        results = executor.execute(client, [order("BUY")],
                                   {"102110": Decimal("109615")})

        assert results["102110"]["filled"]
        assert len(client.placed) == 2
        assert client.cancelled == ["order-1"]

    def test_gives_up_after_max_attempts(self):
        client = FakeClient(fill_on_attempt=None)
        results = executor.execute(client, [order("BUY")],
                                   {"102110": Decimal("109615")})

        assert not results["102110"]["filled"]
        assert len(client.placed) == executor.MAX_ATTEMPTS
        assert len(client.cancelled) == executor.MAX_ATTEMPTS

    def test_never_leaves_an_order_resting_on_failure(self):
        # Every placed order must be accounted for: filled or cancelled.
        # A leftover resting order would be re-counted as a holding on the
        # next run and skew the entire plan.
        client = FakeClient(fill_on_attempt=None)
        executor.execute(client, [order("BUY")], {"102110": Decimal("109615")})

        assert client._open == []

    def test_skips_symbol_when_book_is_abnormal(self):
        client = FakeClient(touch=Touch(bid=Decimal("60000"),
                                        ask=Decimal("60000"),
                                        bid_volume=1, ask_volume=1))
        results = executor.execute(client, [order("SELL")],
                                   {"102110": Decimal("109615")})

        assert not results["102110"]["filled"]
        assert client.placed == []