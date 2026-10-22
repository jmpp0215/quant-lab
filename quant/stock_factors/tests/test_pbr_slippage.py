"""Tests for pbr_slippage.measure, shared with the asset tracker."""

from decimal import Decimal

from quant.stock_factors.scripts.pbr_slippage import measure


class TestMeasure:
    def test_counts_and_signed_slippage(self):
        orders = [
            ("A", "BUY", 10, "1000", 10, "990"),    # bought 1% under limit
            ("B", "SELL", 5, "2000", 2, "2010"),    # partial, sold 0.5% over
            ("C", "BUY", 3, "500", 0, None),        # unfilled
        ]
        closes = {"A": Decimal("1000"), "B": Decimal("2000")}

        m = measure(orders, closes)

        assert (m["filled"], m["partial"], m["unfilled"]) == (1, 1, 1)
        # vs limit, weighted by limit x filled qty: 100 + 20 KRW on 14,000
        assert m["notional"] == Decimal("14000")
        assert m["vs_limit_krw"] == Decimal("120")
        # vs close, weighted by fill notional: 1% of 9,900 + 0.5% of 4,020
        assert m["vs_close_krw"] == Decimal("119.1")
        assert m["close_notional"] == Decimal("9900") + Decimal("4020")

    def test_missing_close_leaves_vs_close_out(self):
        m = measure([("A", "BUY", 1, "100", 1, "100")], {})
        assert m["close_notional"] == 0
        assert m["rows"][0][7] is None
