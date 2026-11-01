"""Tests for execution safety checks."""

from decimal import Decimal

import pytest

from quant import executor, kis_client, toss_client
from quant.broker import OpenOrder, OrderHandle, OrderStatus, Touch
from quant.executor import ExecutionError, ExecutionHalted, OrderState
from quant.kis_client import KisApiError
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

    Each order is tracked as quantity / filled / open. The attempt named by
    fill_on_attempt fills in full at once; every other attempt rests until
    cancelled, holding whatever `executions` scripts as already filled, and
    a cancel closes it with that fill.
    """

    def __init__(self, fill_on_attempt: int | None = 1,
                 touch: Touch | None = None,
                 executions: dict[str, dict] | None = None):
        self.fill_on_attempt = fill_on_attempt
        self.touch = touch or Touch(
            bid=Decimal("109615"), ask=Decimal("109620"),
            bid_volume=1000, ask_volume=1000,
        )
        self.executions = executions or {}
        self.placed: list[dict] = []
        self.cancelled: list[str] = []
        self.orders: dict[str, dict] = {}

    def orderbook(self, client, symbol):
        return self.touch

    def place_order(self, client, order, price):
        attempt = len(self.placed) + 1
        handle = OrderHandle(order_id=f"order-{attempt}")
        self.placed.append({"symbol": order.symbol, "side": order.side,
                            "quantity": order.quantity, "price": price,
                            "order_id": handle.order_id})

        fills = attempt == self.fill_on_attempt
        scripted = self.executions.get(handle.order_id)
        if scripted:
            filled = int(scripted["filledQuantity"])
        else:
            filled = order.quantity if fills else 0
        self.orders[handle.order_id] = {"symbol": order.symbol,
                                        "quantity": order.quantity,
                                        "filled": filled, "open": not fills}
        return handle

    def open_orders(self, client):
        return [OpenOrder(handle=OrderHandle(order_id=oid),
                          symbol=o["symbol"], quantity=o["quantity"],
                          filled_quantity=o["filled"])
                for oid, o in self.orders.items() if o["open"]]

    def cancel(self, client, handle):
        self.cancelled.append(handle.order_id)
        self.orders[handle.order_id]["open"] = False

    def order_status(self, client, handle):
        o = self.orders.get(handle.order_id)
        if o is None:
            return None
        execution = self.executions.get(
            handle.order_id, {"filledQuantity": str(o["filled"])})
        return OrderStatus(quantity=o["quantity"], filled_quantity=o["filled"],
                           open=o["open"], execution=execution)


class FakeClient:
    """Stands in for the client object executor passes through untouched."""
    dry_run = False


class FakeTime:
    """A clock that only moves when the executor sleeps, so polling
    deadlines run their full course instantly and deterministically."""

    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture(autouse=True)
def fake_clock(monkeypatch):
    monkeypatch.setattr(executor, "time", FakeTime())


PRICES = {"102110": Decimal("109615"), "379790": Decimal("109615")}


class TestExecuteRetry:
    def test_fills_on_first_attempt(self):
        b, c = FakeBroker(fill_on_attempt=1), FakeClient()
        results = executor.execute(b, c, [order("BUY")], PRICES)

        assert results["102110"]["filled"]
        assert len(b.placed) == 1
        assert b.cancelled == []

    def test_cancels_and_reprices_when_unfilled(self):
        # The first order rests unfilled; the executor must cancel it
        # before placing another, or the account ends up with both.
        b, c = FakeBroker(fill_on_attempt=2), FakeClient()
        results = executor.execute(b, c, [order("BUY")], PRICES)

        assert results["102110"]["filled"]
        assert len(b.placed) == 2
        assert b.cancelled == ["order-1"]

    def test_gives_up_after_max_attempts(self):
        b, c = FakeBroker(fill_on_attempt=None), FakeClient()
        results = executor.execute(b, c, [order("BUY")], PRICES)

        assert not results["102110"]["filled"]
        assert len(b.placed) == executor.MAX_ATTEMPTS
        assert len(b.cancelled) == executor.MAX_ATTEMPTS

    def test_never_leaves_an_order_resting_on_failure(self):
        # Every placed order must be accounted for: filled or cancelled.
        # A leftover resting order would be re-counted as a holding on the
        # next run and skew the entire plan.
        b, c = FakeBroker(fill_on_attempt=None), FakeClient()
        executor.execute(b, c, [order("BUY")], PRICES)

        assert b.open_orders(c) == []

    def test_reissues_only_the_remainder_of_a_broker_closed_order(self):
        # The broker closes the first order itself (no cancel from us)
        # with 4 of 10 filled: the retry must be for 6, not 10.
        b = FakeBroker(fill_on_attempt=2,
                       executions={"order-1": {"filledQuantity": "4"}})
        place = b.place_order

        def place_and_close_first(client, o, price):
            handle = place(client, o, price)
            if handle.order_id == "order-1":
                b.orders["order-1"]["open"] = False
            return handle

        b.place_order = place_and_close_first
        results = executor.execute(b, FakeClient(), [order("BUY")], PRICES)

        assert b.cancelled == []
        assert [p["quantity"] for p in b.placed] == [10, 6]
        assert results["102110"]["filled_quantity"] == 10

    def test_skips_symbol_when_book_is_abnormal(self):
        b = FakeBroker(touch=Touch(bid=Decimal("60000"), ask=Decimal("60000"),
                                   bid_volume=1, ask_volume=1))
        results = executor.execute(b, FakeClient(), [order("SELL")], PRICES)

        assert not results["102110"]["filled"]
        assert b.placed == []

    def test_dry_run_places_nothing(self):
        # place_order returning None is how a broker signals "dry run";
        # the executor must treat it as done rather than poll for a fill
        # that will never arrive.
        b = FakeBroker()
        b.place_order = lambda client, order, price: None
        results = executor.execute(b, FakeClient(), [order("BUY")], PRICES)

        assert results["102110"]["filled"]
        assert results["102110"]["order_id"] is None
        assert b.cancelled == []


class TestClassify:
    """The order state is derived from quantities the broker reported,
    never from the bare fact that an order left the open list."""

    def status(self, filled=0, open=True, quantity=10):
        return OrderStatus(quantity=quantity, filled_quantity=filled,
                           open=open, execution={})

    def test_resting_without_fills_is_open(self):
        assert executor.classify(self.status(), 10, cancel_sent=False) \
            is OrderState.OPEN

    def test_resting_with_fills_is_partially_filled(self):
        assert executor.classify(self.status(filled=4), 10, cancel_sent=False) \
            is OrderState.PARTIALLY_FILLED

    def test_still_open_after_a_cancel_is_cancel_pending(self):
        # An accepted cancel request is not a closed order.
        assert executor.classify(self.status(filled=4), 10, cancel_sent=True) \
            is OrderState.CANCEL_PENDING

    def test_closed_in_full_is_filled(self):
        assert executor.classify(self.status(filled=10, open=False), 10,
                                 cancel_sent=True) is OrderState.FILLED

    def test_closed_short_is_canceled(self):
        for filled in (0, 4):
            assert executor.classify(self.status(filled=filled, open=False),
                                     10, cancel_sent=True) \
                is OrderState.CANCELED

    def test_no_record_is_unknown(self):
        assert executor.classify(None, 10, cancel_sent=False) \
            is OrderState.UNKNOWN

    def test_quantity_other_than_placed_is_unknown(self):
        assert executor.classify(self.status(quantity=7, open=False), 10,
                                 cancel_sent=False) is OrderState.UNKNOWN

    def test_more_filled_than_ordered_is_unknown(self):
        assert executor.classify(self.status(filled=11, open=False), 10,
                                 cancel_sent=False) is OrderState.UNKNOWN


class TestUnconfirmedFills:
    """The retry loop sizes the remainder only from a terminal state the
    broker confirmed. Before this, an order that left the open list with no
    readable fill was assumed filled in full, and one whose fill could not
    be read after a cancel was assumed empty and reordered in full."""

    def test_unreadable_fill_halts_instead_of_assuming_a_full_fill(self):
        b = FakeBroker(fill_on_attempt=1)
        b.order_status = lambda client, handle: None

        with pytest.raises(ExecutionHalted) as e:
            executor.execute(b, FakeClient(), [order("BUY")], PRICES)

        assert len(b.placed) == 1
        assert e.value.results["102110"]["filled_quantity"] == 0
        assert not e.value.results["102110"]["filled"]

    def test_unreadable_fill_after_cancel_is_not_reordered(self):
        b = FakeBroker(fill_on_attempt=None)
        status = b.order_status
        b.order_status = lambda client, handle: (
            None if handle.order_id in b.cancelled
            else status(client, handle))

        with pytest.raises(ExecutionHalted):
            executor.execute(b, FakeClient(), [order("BUY")], PRICES)

        assert len(b.placed) == 1
        assert b.cancelled == ["order-1"]

    def test_fill_landing_after_the_cancel_request_is_counted(self):
        # The cancel is accepted, but the whole order fills before the
        # exchange confirms it. Reading the fill right after the cancel
        # call would see 0 and buy another 10.
        b = FakeBroker(fill_on_attempt=None)

        def cancel_pending(client, handle):
            b.cancelled.append(handle.order_id)
            b.orders[handle.order_id]["pending"] = True

        def status(client, handle):
            o = b.orders[handle.order_id]
            if o.get("pending"):
                # Pending for one read, then the late fill and the close.
                o["pending"] = False
                return OrderStatus(quantity=10, filled_quantity=0, open=True,
                                   execution={})
            if handle.order_id in b.cancelled:
                o.update(filled=10, open=False)
            return OrderStatus(quantity=10, filled_quantity=o["filled"],
                               open=o["open"],
                               execution={"filledQuantity": str(o["filled"])})

        b.cancel, b.order_status = cancel_pending, status
        results = executor.execute(b, FakeClient(), [order("BUY")], PRICES)

        assert len(b.placed) == 1
        assert results["102110"]["filled"]
        assert results["102110"]["filled_quantity"] == 10

    def test_cancel_never_confirmed_halts(self):
        b = FakeBroker(fill_on_attempt=None)
        b.cancel = lambda client, handle: b.cancelled.append(handle.order_id)

        with pytest.raises(ExecutionHalted, match="cancel pending"):
            executor.execute(b, FakeClient(), [order("BUY")], PRICES)

        assert len(b.placed) == 1

    def test_refused_cancel_on_an_order_that_just_filled(self):
        # KIS rejects a cancel when nothing is left to cancel. That
        # refusal must not abort the run; the status read decides.
        b = FakeBroker(fill_on_attempt=None)

        def cancel_refused(client, handle):
            b.orders[handle.order_id].update(filled=10, open=False)
            raise KisApiError(500, "1", "TEST", "nothing left to cancel")

        b.cancel = cancel_refused
        results = executor.execute(b, FakeClient(), [order("BUY")], PRICES)

        assert len(b.placed) == 1
        assert results["102110"]["filled_quantity"] == 10

    def test_halt_keeps_results_settled_before_it(self):
        b = FakeBroker(fill_on_attempt=1)
        status = b.order_status
        b.order_status = lambda client, handle: (
            None if handle.order_id == "order-2" else status(client, handle))
        second = Order(symbol="379790", name="KODEX 미국S&P500", side="BUY",
                       quantity=5, limit_price=Decimal("109620"))

        with pytest.raises(ExecutionHalted) as e:
            executor.execute(b, FakeClient(), [order("BUY"), second], PRICES)

        assert e.value.results["102110"]["filled_quantity"] == 10
        assert e.value.results["379790"]["filled_quantity"] == 0


class TestExecutionAcrossAttempts:
    """results[symbol]["execution"] is what rebalance_run.record() stores
    as filled_qty / avg_fill_price / commission / tax, and what
    storage.unexplained_cash_change nets out of the day's cash move. It
    must cover every attempt, not just the last one: on 2026-10-01 a
    379790 buy filled 74 + 2 and was recorded as 2 shares @ 16,957."""

    def _run(self, executions, qty=76, fill_on_attempt=2):
        b = FakeBroker(fill_on_attempt=fill_on_attempt, executions=executions)
        results = executor.execute(b, FakeClient(), [order("BUY", qty)],
                                   PRICES)
        return results["102110"]

    def test_combines_a_fill_split_across_attempts(self):
        r = self._run({
            "order-1": {"filledQuantity": "74", "averageFilledPrice": "16949"},
            "order-2": {"filledQuantity": "2", "averageFilledPrice": "16957"},
        })
        assert r["filled"]
        assert r["filled_quantity"] == 76
        ex = r["execution"]
        assert int(ex["filledQuantity"]) == 76
        # (74 x 16,949 + 2 x 16,957) / 76
        expected = (74 * Decimal("16949") + 2 * Decimal("16957")) / 76
        assert abs(Decimal(ex["averageFilledPrice"]) - expected) < Decimal("0.001")

    def test_combined_notional_matches_what_was_paid(self):
        r = self._run({
            "order-1": {"filledQuantity": "74", "averageFilledPrice": "16949"},
            "order-2": {"filledQuantity": "2", "averageFilledPrice": "16957"},
        })
        ex = r["execution"]
        notional = int(ex["filledQuantity"]) * Decimal(ex["averageFilledPrice"])
        paid = 74 * Decimal("16949") + 2 * Decimal("16957")
        assert abs(notional - paid) < Decimal("1")

    def test_sums_fees_across_attempts(self):
        r = self._run({
            "order-1": {"filledQuantity": "74", "averageFilledPrice": "16949",
                        "commission": "188", "tax": "0"},
            "order-2": {"filledQuantity": "2", "averageFilledPrice": "16957",
                        "commission": "5", "tax": "0"},
        })
        assert Decimal(r["execution"]["commission"]) == 193
        assert Decimal(r["execution"]["tax"]) == 0

    def test_no_fees_reported_stays_no_fees(self):
        # KIS returns no per-order fees; a missing figure must not turn
        # into a recorded 0 that looks like a real number.
        r = self._run({
            "order-1": {"filledQuantity": "74", "averageFilledPrice": "16949"},
            "order-2": {"filledQuantity": "2", "averageFilledPrice": "16957"},
        })
        assert r["execution"].get("commission") is None
        assert r["execution"].get("tax") is None

    def test_a_single_attempt_fill_is_passed_through_unchanged(self):
        block = {"filledQuantity": "10", "averageFilledPrice": "109620",
                 "commission": "16"}
        r = self._run({"order-1": block}, qty=10, fill_on_attempt=1)
        assert r["execution"] == block


class TestCancelOpenOrders:
    def test_cancels_everything_resting(self):
        b, c = FakeBroker(fill_on_attempt=None), FakeClient()
        b.place_order(c, order("BUY"), Decimal("109620"))
        b.place_order(c, order("SELL"), Decimal("109610"))

        assert executor.cancel_open_orders(b, c) == 2
        assert b.cancelled == ["order-1", "order-2"]
        assert b.open_orders(c) == []

    def test_refuses_to_plan_while_a_cancel_is_unconfirmed(self):
        # Planning from holdings while our own order can still fill would
        # size the run against a position that is about to change.
        b, c = FakeBroker(fill_on_attempt=None), FakeClient()
        b.place_order(c, order("BUY"), Decimal("109620"))
        b.cancel = lambda client, handle: None

        with pytest.raises(ExecutionError, match="cancel pending"):
            executor.cancel_open_orders(b, c)

class FakeKisClient:
    """Returns KIS-shaped payloads, so the adapter's translation is what
    is under test rather than a hand-written Touch."""

    def __init__(self, book: dict | None = None, dry_run: bool = False,
                 row: dict | None = None, rows: list[dict] | None = None):
        # Real payload captured from ISA/102110 on 2026-08-21, trimmed to
        # the fields the adapter reads. KIS pads absent levels with "0".
        self.book = book if book is not None else {
            "askp1": "109510", "askp2": "109515",
            "bidp1": "109505", "bidp2": "109495",
            "askp_rsqn1": "10188", "bidp_rsqn1": "1",
        }
        self.dry_run = dry_run
        self.cancelled: list[dict] = []
        # A TTTC8001R row for a fully filled order, in the shape of live
        # kis-isa rows read 2026-11-01. rows, when given, replaces the
        # one-row reply (an original plus its cancel rows).
        self.row = row if row is not None else {
            "odno": "0024989600", "ord_qty": "10", "tot_ccld_qty": "10",
            "rmn_qty": "0", "cncl_cfrm_qty": "0", "rjct_qty": "0",
            "avg_prvs": "109510",
        }
        self.rows = rows
        self.queried_odno: str | None = None

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

    def daily_orders(self, start, end, odno=""):
        self.queried_odno = odno
        return {"output1": self.rows if self.rows is not None else [self.row]}

    def buying_power(self, symbol, price):
        # ord_psbl_cash left deliberately different from nrcvb_buy_amt,
        # matching the live kis-isa response that showed they diverge -
        # a regression here should fail loudly rather than pass by luck.
        return {"output": {"ord_psbl_cash": "142107",
                           "nrcvb_buy_amt": "3000000"}}


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

    def _status(self, **row):
        base = {"odno": "0024989600", "ord_qty": "10", "tot_ccld_qty": "0",
                "rmn_qty": "0", "cncl_cfrm_qty": "0", "rjct_qty": "0",
                "avg_prvs": "0"}
        return kis_client.order_status(FakeKisClient(row=base | row),
                                       OrderHandle(order_id="0024989600"))

    def test_status_of_a_filled_order_has_no_fees(self):
        # KIS's order inquiry carries no commission or tax. They stay None
        # so storage records NULL rather than an invented figure.
        status = kis_client.order_status(FakeKisClient(),
                                         OrderHandle(order_id="0024989600"))
        assert (status.quantity, status.filled_quantity) == (10, 10)
        assert not status.open
        ex = status.execution
        assert ex["filledQuantity"] == "10"
        assert ex["averageFilledPrice"] == "109510"
        assert ex["commission"] is None and ex["tax"] is None

    def test_status_queries_the_one_order(self):
        # Filtering by ODNO keeps the row on the first page; this client
        # does not follow pagination.
        client = FakeKisClient()
        kis_client.order_status(client, OrderHandle(order_id="0024989600"))
        assert client.queried_odno == "0024989600"

    def test_unconfirmed_cancel_reads_open(self):
        status = self._status(tot_ccld_qty="4", rmn_qty="6")
        assert status.open and status.filled_quantity == 4

    def _status_with_cancel(self, cancel_qty="6", name="매수취소*", **row):
        base = {"odno": "0024989600", "orgn_odno": "", "ord_qty": "10",
                "tot_ccld_qty": "4", "rmn_qty": "0", "cncl_cfrm_qty": "0",
                "rjct_qty": "0", "avg_prvs": "109510"}
        cancel = {"odno": "0024999900", "orgn_odno": "0024989600",
                  "sll_buy_dvsn_cd_name": name, "ord_qty": cancel_qty,
                  "tot_ccld_qty": "0", "rmn_qty": "0", "cncl_cfrm_qty": "0",
                  "rjct_qty": "0", "avg_prvs": "0"}
        return kis_client.order_status(
            FakeKisClient(rows=[cancel, base | row]),
            OrderHandle(order_id="0024989600"))

    def test_confirmed_cancel_closes_with_its_fill(self):
        # Live shape (2026-10-01 0020682800): original 76 ordered, 74
        # filled, rmn 0, cncl_cfrm_qty 0; the 2 cancelled are on a
        # separate 매수취소* row pointing back via orgn_odno.
        status = self._status_with_cancel()
        assert not status.open and status.filled_quantity == 4

    def test_cancel_accepted_but_not_confirmed_reads_open(self):
        status = self._status_with_cancel(rmn_qty="6")
        assert status.open and status.filled_quantity == 4

    def test_cancel_row_that_does_not_cover_the_shortfall_is_unknown(self):
        assert self._status_with_cancel(cancel_qty="5") is None

    def test_child_row_that_is_not_a_cancel_is_unknown(self):
        assert self._status_with_cancel(name="매수정정*") is None

    def test_rejected_order_closes_unfilled(self):
        status = self._status(rjct_qty="10")
        assert not status.open and status.filled_quantity == 0

    def test_quantities_that_do_not_add_up_are_unknown(self):
        # 4 filled, nothing remaining or cancelled: 6 shares unaccounted.
        assert self._status(tot_ccld_qty="4") is None

    def test_missing_field_is_unknown_not_zero(self):
        client = FakeKisClient()
        del client.row["cncl_cfrm_qty"]
        assert kis_client.order_status(
            client, OrderHandle(order_id="0024989600")) is None

    def test_no_row_is_unknown(self):
        assert self._status(odno="0000000001") is None

    def test_available_cash_reads_nrcvb_buy_amt(self):
        # Not ord_psbl_cash (confirmed live to equal dnca_tot_amt, ignoring
        # unsettled sell proceeds entirely) - nrcvb_buy_amt is what
        # resizing a buy right after a sell must use.
        cash = kis_client.available_cash(
            FakeKisClient(), {"102110": Decimal("109510")})
        assert cash == Decimal("3000000")


class ScriptedKisClient:
    """Drives executor.execute() through the real kis_client adapter
    functions (not FakeBroker), scripting which orders are still resting.

    TestKisAdapters checks each adapter translation in isolation; this
    checks that executor.execute()'s cancel/reprice/poll loop still works
    once wired to kis_client's actual functions end to end - the piece
    RUNBOOK.md flags as unverified against KIS.

    Each order is one TTTC8001R-shaped row. A cancel zeroes the original's
    rmn_qty and adds a separate 취소 row for the remainder pointing back via
    orgn_odno - the shape live rows showed on 2026-11-01.
    """

    def __init__(self, fill_on_attempt: int | None = 2):
        # Orders rest until cancelled on every attempt before
        # fill_on_attempt; None means every attempt rests (never fills).
        self._next_odno = 0
        self.created: list[str] = []
        self.cancelled: list[str] = []
        self.fill_on_attempt = fill_on_attempt
        self.rows: dict[str, dict] = {}

    def orderbook(self, symbol):
        return {"output1": {
            "askp1": "109620", "bidp1": "109615",
            "askp_rsqn1": "4222", "bidp_rsqn1": "300",
        }}

    def create_order(self, symbol, side, order_type, quantity, price=None):
        self._next_odno += 1
        odno = f"{self._next_odno:010d}"
        self.created.append(odno)
        fills = self._next_odno == self.fill_on_attempt
        self.rows[odno] = {
            "odno": odno, "ord_gno_brno": "91252", "pdno": symbol,
            "ord_qty": str(quantity),
            "tot_ccld_qty": str(quantity if fills else 0),
            "rmn_qty": str(0 if fills else quantity),
            "orgn_odno": "", "cncl_cfrm_qty": "0", "rjct_qty": "0",
            "avg_prvs": str(price) if fills else "0",
        }
        return {"output": {"ODNO": odno, "KRX_FWDG_ORD_ORGNO": "91252"}}

    def cancel_order(self, orgn_odno, quantity=0, branch_id=""):
        self.cancelled.append(orgn_odno)
        row = self.rows[orgn_odno]
        # Its own number space: fill_on_attempt counts orders, not cancels.
        cancel_odno = "9" + orgn_odno[1:]
        self.rows[cancel_odno] = {
            "odno": cancel_odno, "orgn_odno": orgn_odno,
            "sll_buy_dvsn_cd_name": "매수취소*", "ord_qty": row["rmn_qty"],
            "tot_ccld_qty": "0", "rmn_qty": "0", "cncl_cfrm_qty": "0",
            "rjct_qty": "0", "avg_prvs": "0",
        }
        row["rmn_qty"] = "0"
        return {"output": {}}

    def list_orders(self):
        return {"output": [r for r in self.rows.values()
                           if int(r["rmn_qty"]) > 0]}

    def daily_orders(self, start, end, odno=""):
        return {"output1": [r for r in self.rows.values()
                            if not odno or odno in (r["odno"], r["orgn_odno"])]}


class TestExecuteAgainstKis:
    def test_fills_on_first_attempt(self):
        client = ScriptedKisClient(fill_on_attempt=1)
        results = executor.execute(kis_client, client, [order("BUY")],
                                   {"102110": Decimal("109615")})

        assert results["102110"]["filled"]
        assert results["102110"]["filled_quantity"] == 10
        assert len(client.created) == 1
        assert client.cancelled == []

    def test_cancels_and_reprices_when_unfilled(self):
        # First order rests through the poll timeout; executor must
        # cancel it via the real kis_client.cancel (org_no included) and
        # reissue for the remainder before a second attempt fills.
        client = ScriptedKisClient(fill_on_attempt=2)
        results = executor.execute(kis_client, client, [order("BUY")],
                                   {"102110": Decimal("109615")})

        assert results["102110"]["filled"]
        assert len(client.created) == 2
        assert client.cancelled == [client.created[0]]

    def test_never_leaves_an_order_resting_on_failure(self):
        # Every attempt rests; after MAX_ATTEMPTS the order must be
        # cancelled, not left open, via the real KIS cancel path.
        client = ScriptedKisClient(fill_on_attempt=None)
        results = executor.execute(kis_client, client, [order("BUY")],
                                   {"102110": Decimal("109615")})

        assert not results["102110"]["filled"]
        assert len(client.created) == executor.MAX_ATTEMPTS
        assert client.cancelled == client.created
        assert client.list_orders()["output"] == []


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

        def buying_power(self, currency="KRW"):
            return {"result": {"cashBuyingPower": "5000000"}}

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

    def test_available_cash_ignores_prices(self):
        # Toss's buying-power endpoint is account-level, not symbol-scoped -
        # the prices dict is only there so kis_client's adapter can match.
        assert toss_client.available_cash(self.Client(), {}) == \
            Decimal("5000000")


class TestTossOrderStatus:
    class Client:
        def __init__(self, open_orders=(), closed_orders=()):
            self.lists = {"OPEN": list(open_orders),
                          "CLOSED": list(closed_orders)}

        def list_orders(self, status="OPEN"):
            return {"result": {"orders": self.lists[status]}}

    handle = OrderHandle(order_id="toss-1")

    def test_open_order_reports_its_partial_fill(self):
        client = self.Client(open_orders=[{
            "orderId": "toss-1", "quantity": "10",
            "execution": {"filledQuantity": "4"}}])
        status = toss_client.order_status(client, self.handle)
        assert status.open and status.filled_quantity == 4

    def test_closed_order_is_final(self):
        execution = {"filledQuantity": "10", "averageFilledPrice": "109620",
                     "commission": "16"}
        client = self.Client(closed_orders=[{
            "orderId": "toss-1", "quantity": "10", "execution": execution}])
        status = toss_client.order_status(client, self.handle)
        assert not status.open and status.filled_quantity == 10
        assert status.execution == execution

    def test_closed_without_a_fill_figure_is_unknown(self):
        # The old path read this as "filled in full".
        client = self.Client(closed_orders=[{
            "orderId": "toss-1", "quantity": "10", "execution": None}])
        assert toss_client.order_status(client, self.handle) is None

    def test_order_in_neither_list_is_unknown(self):
        assert toss_client.order_status(self.Client(), self.handle) is None
