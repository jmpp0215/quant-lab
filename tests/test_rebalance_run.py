"""Tests for the tranche rebalance planner.

`rebalance_run.plan_for_tranche` moves one sleeve from its recorded book
to a target share count, and `main()` now sizes that target off each
broker's no-미수 buying power rather than settled cash. Both decisions
move real money, so the arithmetic is covered case by case here.
"""

from decimal import Decimal

from quant import tranche
from rebalance_run import plan_for_tranche

# Real universe codes so config.is_etf() / config.UNIVERSE name lookups
# behave as they do in production. All prices are multiples of the 5-won
# ETF tick, so round_to_tick() leaves them unchanged.
A, B, C = "102110", "133690", "091170"
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
    buying power -> one sleeve's value -> target shares -> orders."""

    def test_deploys_one_tranche_share_of_buying_power(self):
        # 15,000,000 buyable, three even weights, no holdings yet.
        # One sleeve claims 15,000,000 / len(TRANCHES) and spreads it.
        buying_power = Decimal("15000000")
        weights = {A: THIRD, B: THIRD, C: THIRD}
        prices = {A: Decimal("100000"), B: Decimal("200000"),
                  C: Decimal("10000")}

        value = tranche.tranche_value({}, prices, buying_power)
        assert value == buying_power / len(tranche.config.TRANCHES)

        targets = tranche.target_quantities(weights, value, prices)
        orders = plan_for_tranche({}, targets, prices)

        assert all(o.side == "BUY" for o in orders)
        spent = sum(o.notional for o in orders)
        # Whole-share rounding only ever leaves money unspent.
        assert spent <= value
        assert spent > value * Decimal("0.9")
