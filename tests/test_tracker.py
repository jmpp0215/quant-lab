"""Tests for the asset tracker's whole-portfolio figures."""

from datetime import date
from decimal import Decimal

import pytest

from quant import tracker


def D(x) -> Decimal:
    return Decimal(str(x))


def snaps(*pairs):
    return [(d, D(v)) for d, v in pairs]


class TestGrowthIndex:
    def test_single_account_chains_returns(self):
        idx = tracker.growth_index(
            {"a": snaps(("d1", 100), ("d2", 110), ("d3", 121))}, {})

        assert idx == [("d1", 1), ("d2", D("1.1")), ("d3", D("1.21"))]

    def test_deposit_is_not_a_gain(self):
        idx = tracker.growth_index(
            {"a": snaps(("d1", 100), ("d2", 160))},
            {"a": snaps(("d2", 50))})

        assert idx[-1] == ("d2", D("1.1"))

    def test_deposit_on_missed_day_nets_on_next_snapshot(self):
        idx = tracker.growth_index(
            {"a": snaps(("d1", 100), ("d3", 160)), "b": snaps(("d2", 100))},
            {"a": snaps(("d2", 50))})

        # d2: a has no snapshot, b's first snapshot -> no gain yet.
        # d3: a gains 10 on a base of 100 (a) + 100 (b carried).
        assert [v for _, v in idx] == [1, 1, D("1.05")]

    def test_later_account_enlarges_base_without_jump(self):
        idx = tracker.growth_index(
            {"a": snaps(("d1", 100), ("d2", 110), ("d3", 110)),
             "b": snaps(("d2", 1000), ("d3", 1100))}, {})

        # d3: b gains 100 on a base of 110 + 1000.
        assert idx[1] == ("d2", D("1.1"))
        assert idx[2][1] == D("1.1") * (1 + D(100) / D(1110))

    def test_deposit_before_first_snapshot_is_ignored(self):
        idx = tracker.growth_index(
            {"a": snaps(("d2", 100), ("d3", 100))}, {"a": snaps(("d1", 100))})

        assert [v for _, v in idx] == [1, 1]

    def test_empty(self):
        assert tracker.growth_index({}, {}) == []


class TestDrawdown:
    IDX = [("d1", D(1)), ("d2", D("1.2")), ("d3", D("0.9")), ("d4", D("1.5"))]

    def test_series_is_relative_to_running_peak(self):
        assert tracker.drawdown_series(self.IDX) == [
            ("d1", 0), ("d2", 0), ("d3", D("-0.25")), ("d4", 0)]

    def test_max_drawdown(self):
        assert tracker.max_drawdown(self.IDX) == D("-0.25")

    def test_max_drawdown_empty(self):
        assert tracker.max_drawdown([]) is None


class TestVolatility:
    def test_constant_returns_have_zero_vol(self):
        idx = [("d1", D(1)), ("d2", D("1.1")), ("d3", D("1.21"))]
        assert tracker.volatility(idx) == 0

    def test_annualises_sample_stdev(self):
        idx = [("d1", D(1)), ("d2", D("1.01")), ("d3", D("1.01")),
               ("d4", D("1.0201"))]
        # returns 0.01, 0, 0.01: mean 1/150, sample var 1/30000
        expected = (D(1) / D(30000)).sqrt() * D(252).sqrt()
        assert tracker.volatility(idx) == pytest.approx(expected)

    def test_too_few_points(self):
        assert tracker.volatility([("d1", D(1)), ("d2", D(2))]) is None


class TestHoldings:
    def test_sums_same_name_across_accounts_and_adds_cash(self):
        rows = [
            {"total": D(1300), "cash": D(100), "positions": [
                {"symbol": "A", "name": "에이", "qty": 2, "price": "500"},
                {"symbol": "B", "name": "비", "qty": 1, "price": "200"}]},
            {"total": D(450), "cash": D(50), "positions": [
                {"symbol": "A", "name": "에이", "qty": 1, "price": "400"}]},
        ]

        assert tracker.holdings(rows) == [
            ("에이", D(1400)), ("비", D(200)), ("현금", D(150))]

    def test_foreign_positions_use_implied_rate(self):
        # 1000 KRW of total is foreign: 2 + 3 USD -> 200 KRW per USD.
        rows = [{"total": D(1500), "cash": D(300), "positions": [
            {"symbol": "K", "name": "케이", "qty": 1, "price": "200"},
            {"symbol": "X", "name": "X", "qty": 1, "price": "2", "currency": "USD"},
            {"symbol": "Y", "name": "Y", "qty": 1, "price": "3", "currency": "USD"}]}]

        assert dict(tracker.holdings(rows)) == {
            "Y": D(600), "X": D(400), "현금": D(300), "케이": D(200)}

    def test_hhi(self):
        assert tracker.hhi([D("0.5"), D("0.5")]) == D("0.5")
        assert tracker.hhi([D(1)]) == 1


class TestMonthsToGoal:
    def test_already_reached(self):
        assert tracker.months_to_goal(D(100), D(100), D(0), D(0)) == 0

    def test_contributions_only(self):
        assert tracker.months_to_goal(D(0), D(100), D(30), D(0)) == 4

    def test_growth_shortens_it(self):
        # 12% a year compounds 100 past 112 just after month 12.
        assert tracker.months_to_goal(D(100), D("111.9"), D(0), D("0.12")) == 12

    def test_unreachable(self):
        assert tracker.months_to_goal(D(100), D(200), D(0), D(0)) is None

    def test_month_after(self):
        assert tracker.month_after(date(2026, 10, 31), 3) == "2027-01"
        assert tracker.month_after(date(2026, 1, 15), 0) == "2026-01"
