"""Tests for the tranche rebalance planner.

`rebalance_run.plan_for_tranche` moves one sleeve from its recorded book
to a target share count, and `main()` now sizes that target off each
broker's no-미수 buying power rather than settled cash. Both decisions
move real money, so the arithmetic is covered case by case here.
"""

from decimal import Decimal

import pytest

from quant import tranche
from rebalance_run import confirm, plan_for_tranche

# Real universe codes so config.is_etf() / config.UNIVERSE name lookups
# behave as they do in production. All prices are multiples of the 5-won
# ETF tick, so round_to_tick() leaves them unchanged.
A, B, C = "102110", "133690", "091170"
D = "379790"
THIRD = Decimal("1") / 3


class TestPlanForTranche:
    def test_empty_book_buys_every_target(self):
        orders = plan_for_tranche(
            book={},
            targets={A: 16, B: 8, C: 166},
            prices={A: Decimal("100000"), B: Decimal("200000"),
                    C: Decimal("10000")},
        )
        # plan_for_tranche walks symbols in sorted order.
        assert [(o.symbol, o.side, o.quantity) for o in orders] == [
            (C, "BUY", 166), (A, "BUY", 16), (B, "BUY", 8)
        ]

    def test_sells_are_emitted_before_buys(self):
        # AAA trims, DDD exits entirely, BBB is new - the two sells must
        # come first so their proceeds are available for the buy.
        orders = plan_for_tranche(
            book={A: 20, "005930": 5},
            targets={A: 16, B: 8},
            prices={A: Decimal("100000"), B: Decimal("200000"),
                    "005930": Decimal("70000")},
        )
        sides = [o.side for o in orders]
        assert sides == ["SELL", "SELL", "BUY"]

    def test_skips_orders_below_the_minimum(self):
        # 1 share @ 10,000 = 10,000 KRW, under MIN_ORDER_KRW.
        orders = plan_for_tranche(
            book={},
            targets={A: 1},
            prices={A: Decimal("10000")},
        )
        assert orders == []

    def test_skips_a_symbol_with_no_price(self):
        orders = plan_for_tranche(
            book={},
            targets={A: 5, B: 5},
            prices={A: Decimal("100000")},
        )
        assert [o.symbol for o in orders] == [A]

    def test_no_order_when_book_already_matches_target(self):
        orders = plan_for_tranche(
            book={A: 16},
            targets={A: 16},
            prices={A: Decimal("100000")},
        )
        assert orders == []


class TestFirstTrancheSizing:
    """The end-to-end path main() runs for a first, all-cash tranche:
    buying power -> one sleeve's budget -> target shares -> orders."""

    def test_deploys_one_tranche_share_of_buying_power(self):
        # 15,000,000 buyable, three even weights, no holdings yet.
        # One sleeve claims 15,000,000 / len(TRANCHES) and spreads it.
        buying_power = Decimal("15000000")
        weights = {A: THIRD, B: THIRD, C: THIRD}
        prices = {A: Decimal("100000"), B: Decimal("200000"),
                  C: Decimal("10000")}

        budget = tranche.sleeve_budget({}, 0, prices, buying_power)
        value = budget.value
        assert value == buying_power / len(tranche.config.TRANCHES)

        targets = tranche.target_quantities(weights, value, prices)
        orders = plan_for_tranche({}, targets, prices)

        assert all(o.side == "BUY" for o in orders)
        spent = sum(o.notional for o in orders)
        # Whole-share rounding only ever leaves money unspent.
        assert spent <= value
        assert spent > value * Decimal("0.9")


class TestPostSellResize:
    """main() re-sizes the buys after the sells fill, from fresh buying
    power. A sell only moves value from the sleeve into the pool, so the
    1/N-of-account target - and the buy plan - should not move. The old
    own-holdings + pool/N rule cut kis-isa tranche 0's buys on 2026-10-01
    from 46/5/88 to 33/3/76 at this step."""

    PRICES = {A: Decimal("110455"), B: Decimal("184425"),
              C: Decimal("15750"), D: Decimal("16950")}
    WEIGHTS = {A: THIRD, C: THIRD, D: THIRD}
    # Books before tranche 0's 2026-10-01 run; buying power 5,721,586.
    BOOKS = {
        0: {C: 75, A: 12, B: 5, D: 24},
        5: {C: 75, A: 11, B: 5, D: 23},
        10: {C: 75, A: 11, B: 4, D: 23},
    }
    CASH = Decimal("5721586")

    def _buys(self, books, cash):
        budget = tranche.sleeve_budget(books, 0, self.PRICES, cash)
        targets = tranche.target_quantities(self.WEIGHTS, budget.value,
                                            self.PRICES)
        return {o.symbol: o.quantity
                for o in plan_for_tranche(books[0], targets, self.PRICES)
                if o.side == "BUY"}

    def test_pre_trade_plan_sells_the_dropped_pick(self):
        budget = tranche.sleeve_budget(self.BOOKS, 0, self.PRICES, self.CASH)
        targets = tranche.target_quantities(self.WEIGHTS, budget.value,
                                            self.PRICES)
        orders = plan_for_tranche(self.BOOKS[0], targets, self.PRICES)
        assert [(o.side, o.symbol, o.quantity) for o in orders
                if o.side == "SELL"] == [("SELL", B, 5)]

    def test_buys_are_unchanged_after_the_sell_fills(self):
        before = self._buys(self.BOOKS, self.CASH)
        # 5 x 133690 sold at the planning price, proceeds now buyable.
        after_books = self.BOOKS | {0: {C: 75, A: 12, D: 24}}
        after = self._buys(after_books, self.CASH + 5 * self.PRICES[B])
        assert after == before

    def test_buys_fit_in_the_cash_actually_available(self):
        after_books = self.BOOKS | {0: {C: 75, A: 12, D: 24}}
        cash = self.CASH + 5 * self.PRICES[B]
        buys = self._buys(after_books, cash)
        assert sum(q * self.PRICES[s] for s, q in buys.items()) <= cash


class TestConfirm:
    """The last gate before real orders: [y/n], same as run_pbr_live.py.
    Only an explicit y proceeds."""

    @pytest.mark.parametrize("answer", ["y", "Y", " y "])
    def test_y_proceeds(self, monkeypatch, answer):
        monkeypatch.setattr("builtins.input", lambda _: answer)
        assert confirm("Rebalance tranche 0?")

    @pytest.mark.parametrize("answer", ["n", "", "yes", "no", "x"])
    def test_anything_else_aborts(self, monkeypatch, answer):
        monkeypatch.setattr("builtins.input", lambda _: answer)
        assert not confirm("Rebalance tranche 0?")
