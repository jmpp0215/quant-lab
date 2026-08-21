"""Tests for execution safety checks."""

from decimal import Decimal

import pytest

from quant import executor, kis_client, toss_client
from quant.broker import OpenOrder, OrderHandle, Touch
from quant.executor import ExecutionError
from quant.rebalance import Order


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


class FakeBroker:
    """A broker module stand-in implementing quant/broker.py's interface.

    Records every call so the test can assert on the sequence, and lets
    each scenario script when a fill happens. Substituting this for a real
    broker module is the whole point of the interface: executor never sees
    a broker's response shape.
    """

    def __init__(self, fill_on_attempt: int | None = 1,
                 touch: Touch | None = None):
        self.fill_on_attempt = fill_on_attempt
        self.touch = touch or Touch(
            bid=Decimal("109615"), ask=Decimal("109620"),
            bid_volume=1000, ask_volume=1000,
        )
        self.placed: list[dict] = []
        self.cancelled: list[str] = []
        self._open: list[OpenOrder] = []

    def orderbook(self, client, symbol):
        return self.touch

    def place_order(self, client, order, price):
        attempt = len(self.placed) + 1
        handle = OrderHandle(order_id=f"order-{attempt}")
        self.placed.append({"symbol": order.symbol, "side": order.side,
                            "quantity": order.quantity, "price": price,
                            "order_id": handle.order_id})

        # An order rests on the book unless this attempt is the one
        # scripted to fill.
        if attempt != self.fill_on_attempt:
            self._open.append(OpenOrder(handle=handle, symbol=order.symbol,
                                        quantity=order.quantity,
                                        filled_quantity=0))
        return handle

    def open_orders(self, client):
        return list(self._open)

    def cancel(self, client, handle):
        self.cancelled.append(handle.order_id)
        self._open = [o for o in self._open
                      if o.handle.order_id != handle.order_id]

    def execution_for(self, client, handle):
        return {}


class FakeClient:
    """Stands in for the client object executor passes through untouched."""
    dry_run = False


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Collapse the polling waits so tests run instantly."""
    monkeypatch.setattr(executor.time, "sleep", lambda _: None)
    monkeypatch.setattr(executor, "FILL_TIMEOUT", 1)


class TestExecuteRetry:
    def test_fills_on_first_attempt(self):
        b, c = FakeBroker(fill_on_attempt=1), FakeClient()
        results = executor.execute(b, c, [order("BUY")],
                                   {"102110": Decimal("109615")})

        assert results["102110"]["filled"]
        assert len(b.placed) == 1
        assert b.cancelled == []

    def test_cancels_and_reprices_when_unfilled(self):
        # The first order rests unfilled; the executor must cancel it
        # before placing another, or the account ends up with both.
        b, c = FakeBroker(fill_on_attempt=2), FakeClient()
        results = executor.execute(b, c, [order("BUY")],
                                   {"102110": Decimal("109615")})

        assert results["102110"]["filled"]
        assert len(b.placed) == 2
        assert b.cancelled == ["order-1"]

    def test_gives_up_after_max_attempts(self):
        b, c = FakeBroker(fill_on_attempt=None), FakeClient()
        results = executor.execute(b, c, [order("BUY")],
                                   {"102110": Decimal("109615")})

        assert not results["102110"]["filled"]
        assert len(b.placed) == executor.MAX_ATTEMPTS
        assert len(b.cancelled) == executor.MAX_ATTEMPTS

    def test_never_leaves_an_order_resting_on_failure(self):
        # Every placed order must be accounted for: filled or cancelled.
        # A leftover resting order would be re-counted as a holding on the
        # next run and skew the entire plan.
        b, c = FakeBroker(fill_on_attempt=None), FakeClient()
        executor.execute(b, c, [order("BUY")], {"102110": Decimal("109615")})

        assert b._open == []

    def test_skips_symbol_when_book_is_abnormal(self):
        b = FakeBroker(touch=Touch(bid=Decimal("60000"), ask=Decimal("60000"),
                                   bid_volume=1, ask_volume=1))
        results = executor.execute(b, FakeClient(), [order("SELL")],
                                   {"102110": Decimal("109615")})

        assert not results["102110"]["filled"]
        assert b.placed == []

    def test_dry_run_places_nothing(self):
        # place_order returning None is how a broker signals "dry run";
        # the executor must treat it as done rather than poll for a fill
        # that will never arrive.
        b = FakeBroker()
        b.place_order = lambda client, order, price: None
        results = executor.execute(b, FakeClient(), [order("BUY")],
                                   {"102110": Decimal("109615")})

        assert results["102110"]["filled"]
        assert results["102110"]["order_id"] is None
        assert b.cancelled == []


class TestCancelOpenOrders:
    def test_cancels_everything_resting(self):
        b, c = FakeBroker(fill_on_attempt=None), FakeClient()
        b.place_order(c, order("BUY"), Decimal("109620"))
        b.place_order(c, order("SELL"), Decimal("109610"))

        assert executor.cancel_open_orders(b, c) == 2
        assert b.cancelled == ["order-1", "order-2"]
        assert b._open == []

class FakeKisClient:
    """Returns KIS-shaped payloads, so the adapter's translation is what
    is under test rather than a hand-written Touch."""

    def __init__(self, book: dict | None = None, dry_run: bool = False):
        # Real payload captured from ISA/102110 on 2026-08-21, trimmed to
        # the fields the adapter reads. KIS pads absent levels with "0".
        self.book = book if book is not None else {
            "askp1": "109510", "askp2": "109515",
            "bidp1": "109505", "bidp2": "109495",
            "askp_rsqn1": "10188", "bidp_rsqn1": "1",
        }
        self.dry_run = dry_run
        self.cancelled: list[dict] = []

    def orderbook(self, symbol):
        return {"output1": self.book}

    def create_order(self, symbol, side, order_type, quantity, price=None):
        if self.dry_run:
            return {"dryRun": True, "request": {}}
        return {"output": {"ODNO": "0024989600",
                           "KRX_FWDG_ORD_ORGNO": "91252"}}

    def cancel_order(self, orgn_odno, quantity=0, branch_id=""):
        self.cancelled.append({"orgn_odno": orgn_odno, "quantity": quantity,
                               "branch_id": branch_id})
        return {"output": {}}

    def list_orders(self):
        return {"output": [{"odno": "0024989600", "ord_gno_brno": "91252",
                            "pdno": "102110", "ord_qty": "10",
                            "tot_ccld_qty": "4"}]}

    def daily_orders(self, start, end):
        return {"output1": [{"odno": "0024989600", "tot_ccld_qty": "10",
                             "avg_prvs": "109510"}]}


class TestKisAdapters:
    def test_orderbook_maps_kis_fields_to_touch(self):
        touch = kis_client.orderbook(FakeKisClient(), "102110")
        assert touch.ask == Decimal("109510")
        assert touch.bid == Decimal("109505")
        assert touch.ask_volume == 10188
        assert touch.bid_volume == 1

    def test_empty_book_reads_as_none_not_zero(self):
        # KIS pads an empty side with "0" where Toss omits the level.
        # Passing 0 through as a price would turn "no ask" into "the ask
        # is zero", which limit_price_for would reject as a 100% move -
        # the right refusal for entirely the wrong reason.
        touch = kis_client.orderbook(
            FakeKisClient(book={"askp1": "0", "bidp1": "0",
                                "askp_rsqn1": "0", "bidp_rsqn1": "0"}),
            "102110",
        )
        assert touch.ask is None and touch.bid is None
        with pytest.raises(ExecutionError, match="no ask"):
            executor.limit_price_for(order("BUY"), touch, Decimal("109615"))

    def test_place_carries_both_halves_of_the_handle(self):
        handle = kis_client.place_order(FakeKisClient(), order("BUY"),
                                        Decimal("109510"))
        assert handle.order_id == "0024989600"
        # Without org_no the order cannot be cancelled later.
        assert handle.org_no == "91252"

    def test_dry_run_returns_no_handle(self):
        assert kis_client.place_order(FakeKisClient(dry_run=True),
                                      order("BUY"), Decimal("109510")) is None

    def test_cancel_sends_org_no_alongside_the_id(self):
        # The regression this whole handle design exists to prevent: KIS
        # silently cannot act on an order id by itself.
        client = FakeKisClient()
        kis_client.cancel(client, OrderHandle(order_id="0024989600",
                                              org_no="91252"))
        assert client.cancelled == [{"orgn_odno": "0024989600",
                                     "quantity": 0, "branch_id": "91252"}]

    def test_cancel_refuses_a_handle_without_org_no(self):
        with pytest.raises(ValueError, match="org_no"):
            kis_client.cancel(FakeKisClient(),
                              OrderHandle(order_id="0024989600"))

    def test_open_orders_reports_partial_fills(self):
        resting = kis_client.open_orders(FakeKisClient())
        assert len(resting) == 1
        assert resting[0].handle == OrderHandle(order_id="0024989600",
                                                org_no="91252")
        assert resting[0].quantity == 10
        assert resting[0].filled_quantity == 4

    def test_execution_has_no_fees(self):
        # KIS's order inquiry carries no commission or tax. They stay None
        # so storage records NULL rather than an invented figure.
        ex = kis_client.execution_for(FakeKisClient(),
                                      OrderHandle(order_id="0024989600"))
        assert ex["filledQuantity"] == "10"
        assert ex["averageFilledPrice"] == "109510"
        assert ex["commission"] is None and ex["tax"] is None


class TestTossAdaptersUnchanged:
    """The Toss path must behave exactly as it did before the refactor."""

    class Client:
        def __init__(self):
            self.cancelled = []

        def get(self, path, params=None, need_account=False):
            assert path == "/api/v1/orderbook"
            return {"result": {
                "asks": [{"price": "109620", "volume": "4222"}],
                "bids": [{"price": "109615", "volume": "300"}],
            }}

        def create_order(self, symbol, side, order_type, quantity, price=None):
            return {"result": {"orderId": "toss-1"}}

        def cancel_order(self, order_id):
            self.cancelled.append(order_id)

    def test_orderbook(self):
        touch = toss_client.orderbook(self.Client(), "102110")
        assert (touch.ask, touch.bid) == (Decimal("109620"), Decimal("109615"))
        assert (touch.ask_volume, touch.bid_volume) == (4222, 300)

    def test_place_handle_has_no_org_no(self):
        handle = toss_client.place_order(self.Client(), order("BUY"),
                                         Decimal("109620"))
        assert handle == OrderHandle(order_id="toss-1", org_no=None)

    def test_cancel_uses_id_alone(self):
        client = self.Client()
        toss_client.cancel(client, OrderHandle(order_id="toss-1"))
        assert client.cancelled == ["toss-1"]
