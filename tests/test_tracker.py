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


class TestImpliedRate:
    def test_foreign_share_of_total_over_own_currency_value(self):
        row = {"total": D(1500), "cash": D(300), "positions": [
            {"symbol": "K", "qty": 1, "price": "200"},
            {"symbol": "X", "qty": 2, "price": "2.5", "currency": "USD"}]}
        assert tracker.implied_rate(row) == D(200)

    def test_none_without_foreign_positions(self):
        row = {"total": D(100), "cash": D(0), "positions": [
            {"symbol": "K", "qty": 1, "price": "100"}]}
        assert tracker.implied_rate(row) is None


class TestFxSplit:
    def test_price_and_fx_effects_sum_to_krw_gain(self):
        # Bought $100 at 1,500 (150,000원); now $110 at 1,300 (143,000원).
        [s] = tracker.fx_split([{"symbol": "A", "name": "에이", "qty": 1,
                                 "price": "110", "currency": "USD",
                                 "cost": "100", "cost_krw": "150000"}], D(1300))

        assert s.buy_rate == 1500
        assert s.price_effect == D(15000)       # +$10 at 1,500
        assert s.fx_effect == D(-22000)         # $110 x (1,300 - 1,500)
        assert s.price_effect + s.fx_effect == s.value_krw - s.cost_krw

    def test_skips_krw_and_costless_positions(self):
        positions = [{"symbol": "K", "qty": 1, "price": "100"},
                     {"symbol": "X", "qty": 1, "price": "1", "currency": "USD"}]
        assert tracker.fx_split(positions, D(1300)) == []


class TestBenchmarkSeries:
    # newest-first, like the candle cache; no candle on 10-03 (holiday)
    CANDLES = [{"timestamp": f"2026-10-0{d}T00:00:00.000+09:00", "closePrice": c}
               for d, c in [(5, "110"), (2, "105"), (1, "100")]]

    def test_returns_from_first_date_using_last_close_at_or_before(self):
        dates = ["2026-10-01", "2026-10-03", "2026-10-05"]
        assert tracker.benchmark_series(self.CANDLES, dates) == [
            0, D("0.05"), D("0.1")]

    def test_no_close_before_first_date(self):
        assert tracker.benchmark_series(self.CANDLES, ["2026-09-30"]) == [None]


class TestContributions:
    @staticmethod
    def snap(date, cash, positions, total=None):
        value = sum((D(p["price"]) * p["qty"] for p in positions), D(0))
        return {"date": date, "cash": D(cash), "positions": positions,
                "total": D(total) if total is not None else D(cash) + value}

    def total(self, rows):
        return sum((r["amount"] for r in rows), D(0))

    def test_price_moves_by_holding_and_sum_matches_gain(self):
        a = [self.snap("d1", 100, [{"symbol": "A", "name": "에이", "qty": 10, "price": "100"},
                                   {"symbol": "B", "name": "비", "qty": 5, "price": "200"}]),
             self.snap("d2", 100, [{"symbol": "A", "name": "에이", "qty": 10, "price": "110"},
                                   {"symbol": "B", "name": "비", "qty": 5, "price": "190"}])]
        rows = tracker.contributions({"x": a}, {}, {}, "d1", "d2")

        assert {(r["name"], r["amount"]) for r in rows} == {("에이", D(100)), ("비", D(-50))}
        assert self.total(rows) == D(50)

    def test_deposit_is_not_gain_and_dividend_has_its_own_row(self):
        a = [self.snap("d1", 100, [{"symbol": "A", "qty": 1, "price": "100"}]),
             self.snap("d2", 1180, [{"symbol": "A", "qty": 1, "price": "100"}])]
        rows = tracker.contributions({"x": a}, {"x": [("d2", D(1000))]},
                                     {"x": [("d2", D(80))]}, "d1", "d2")

        assert [(r["name"], r["kind"], r["amount"]) for r in rows] == [("배당", "dividend", D(80))]

    def test_foreign_split_into_price_and_rate(self):
        # $10 x 10 shares at 1,300 -> $11 at 1,200; $100 cash
        def usd(date, price, rate):
            return {"date": date, "cash": D(100) * D(rate),
                    "total": (D(100) + D(price) * 10) * D(rate),
                    "positions": [{"symbol": "X", "name": "엑스", "qty": 10,
                                   "price": str(price), "currency": "USD"}]}
        rows = tracker.contributions({"o": [usd("d1", 10, 1300), usd("d2", 11, 1200)]},
                                     {}, {}, "d1", "d2")
        got = {(r["name"], r["kind"]): r["amount"] for r in rows}

        assert got[("엑스", "stock")] == D(13000)        # +$10 at 1,300
        assert got[("환율", "fx")] == D(-11000) + D(-10000)  # $110 and $100 cash x -100
        assert self.total(rows) == D(210 * 1200 - 200 * 1300)

    def test_trade_slippage_lands_in_other(self):
        # bought 1 B at 100 (close 100) but cash fell by 103
        a = [self.snap("d1", 1000, []),
             self.snap("d2", 897, [{"symbol": "B", "qty": 1, "price": "100"}])]
        rows = tracker.contributions({"x": a}, {}, {}, "d1", "d2")

        assert [(r["name"], r["amount"]) for r in rows] == [("기타", D(-3))]

    def test_only_steps_ending_in_the_window(self):
        a = [self.snap(d, 0, [{"symbol": "A", "qty": 1, "price": str(p)}])
             for d, p in [("2026-09-30", 100), ("2026-10-01", 110), ("2026-10-02", 130)]]
        rows = tracker.contributions({"x": a}, {}, {}, "2026-10-02", "2026-10-31")
        assert self.total(rows) == D(20)


class TestOverseasGainsTax:
    def test_net_gain_over_deduction_taxed_at_22_percent(self):
        rows = [{"pnl": "2000", "rate": "1400"},   # 2,800,000
                {"pnl": "-100", "rate": "1300"}]   # -130,000
        t = tracker.overseas_gains_tax(rows)
        assert t["pnl_krw"] == D(2670000)
        assert t["taxable"] == D(170000)
        assert t["tax"] == D(37400)

    def test_under_deduction_is_untaxed(self):
        t = tracker.overseas_gains_tax([{"pnl": "1000", "rate": "1400"}])
        assert (t["taxable"], t["tax"]) == (0, 0)


class TestRollingFan:
    def test_quantiles_over_every_start(self):
        # steady +1%/day: every window k days long returns 1.01^k - 1
        values = [1.01 ** i for i in range(50)]
        fan = tracker.rolling_fan(values, horizon=5, min_paths=10)

        assert fan["days"] == [0, 1, 2, 3, 4, 5]
        assert fan["p50"][3] == pytest.approx(1.01 ** 3 - 1)
        assert fan["p5"][3] == pytest.approx(fan["p95"][3])
        assert fan["n"][5] == 45

    def test_stops_when_too_few_windows(self):
        fan = tracker.rolling_fan([1.0] * 12, horizon=100, min_paths=10)
        assert fan["days"][-1] == 2

    def test_cost_charged_at_start_and_every_period(self):
        fan = tracker.rolling_fan([1.0] * 100, horizon=25, min_paths=10,
                                  cost=0.01, cost_every=20)
        assert fan["p50"][0] == pytest.approx(-0.01)
        assert fan["p50"][19] == pytest.approx(-0.01)
        assert fan["p50"][20] == pytest.approx(0.99 ** 2 - 1)

    def test_percentile_interpolates_and_clamps(self):
        fan = {"days": [10], "p5": [-0.1], "p25": [0.0], "p50": [0.05],
               "p75": [0.1], "p95": [0.2]}
        assert tracker.fan_percentile(fan, 10, 0.025) == pytest.approx(37.5)
        assert tracker.fan_percentile(fan, 10, -0.5) == 0.0
        assert tracker.fan_percentile(fan, 10, 0.5) == 100.0
        assert tracker.fan_percentile(fan, 11, 0.0) is None


ENTRY = """---
recorded: 2026-10-19
resolve_by: 2026-12-31
confidence: 50
scored: true
after_the_fact: false
status: open                  # open | resolved
outcome:                      # hit | miss | partial | void
---

# 앤트로픽은 2026년 안에 상장한다

## 예측
- **주장:** 앤트로픽이 2026년 안에 미국 거래소에 상장한다.
"""


class TestPredictions:
    def test_parses_frontmatter_title_and_claim(self):
        e = tracker.parse_prediction(ENTRY)
        assert (e["resolve_by"], e["confidence"], e["status"], e["outcome"]) == (
            "2026-12-31", 50, "open", None)
        assert e["scored"] is True and e["after_the_fact"] is False
        assert e["title"] == "앤트로픽은 2026년 안에 상장한다"
        assert e["claim"] == "앤트로픽이 2026년 안에 미국 거래소에 상장한다."

    def test_stats_count_only_scored_resolved(self):
        def e(outcome, conf, scored=True, status="resolved"):
            return {"outcome": outcome, "confidence": conf, "scored": scored, "status": status}
        entries = [e("hit", 70), e("miss", 75), e("partial", 30), e("void", 50),
                   e("hit", 90, scored=False), e(None, 60, status="open")]
        st = tracker.prediction_stats(entries)
        assert st["resolved"] == 3
        assert st["hit_rate"] == pytest.approx(0.5)
        assert st["by_confidence"] == [{"bucket": 30, "n": 1, "hit_rate": 0.5},
                                       {"bucket": 70, "n": 2, "hit_rate": 0.5}]


class TestIsaPaid:
    DEPOSITS = [("2026-10-20", D(999)), ("2026-11-03", D(1000000)), ("2027-01-05", D(500000))]

    def test_adds_deposits_after_the_entry_date(self):
        p = tracker.isa_paid(D(13510023), D(2000000), "2026-10-23", self.DEPOSITS,
                             date(2026, 12, 1))
        assert (p["total"], p["this_year"], p["added"]) == (D(15010023), D(3000000), D(1500000))

    def test_new_year_counts_only_that_years_deposits(self):
        p = tracker.isa_paid(D(13510023), D(2000000), "2026-10-23", self.DEPOSITS,
                             date(2027, 2, 1))
        assert p["this_year"] == D(500000)

    def test_unknown_this_year_stays_unknown_in_the_entry_year(self):
        p = tracker.isa_paid(D(100), None, "2026-10-23", [], date(2026, 12, 1))
        assert (p["total"], p["this_year"]) == (D(100), None)
