import pytest

from quant import stress

DAYS = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]


def s(values, days=DAYS):
    return (list(days), list(values))


def row(symbol, value, asset="주식", region="한국", currency="KRW", account="a", name=None):
    return {"account": account, "symbol": symbol, "name": name or symbol or "현금",
            "value": value, "asset": asset, "region": region, "currency": currency}


SERIES = {
    "005930.KS": s([100, 90, 80, 85]),
    "^KS11": s([1000, 950, 900, 1000]),
    "^KQ11": s([500, 400, 450, 500]),
    "AMZN": s([10, 9, 8, 9]),
    "^GSPC": s([100, 100, 100, 100]),
    stress.FX: s([1000, 1100, 1200, 1100]),
}


class TestSource:
    def test_own_history_used_when_it_covers_start(self):
        assert stress.source(row("005930", 1), {"005930": "005930.KS"}, SERIES, "2020-01-02") \
            == ("005930.KS", False, False)

    def test_us_stock_converts_at_fx(self):
        r = row("AMZN", 1, region="미국", currency="USD")
        assert stress.source(r, {"AMZN": "AMZN"}, SERIES, "2020-01-02") == ("AMZN", True, False)

    def test_listed_after_start_falls_back_to_market_index(self):
        assert stress.source(row("005930", 1), {"005930": "005930.KS"}, SERIES, "2019-06-01") is None
        series = SERIES | {"^KS11": s([1, 1], ["2019-01-02", "2019-01-03"])}
        assert stress.source(row("005930", 1), {"005930": "005930.KS"}, series, "2019-06-01") \
            == ("^KS11", False, True)

    def test_kosdaq_listing_uses_kosdaq_proxy(self):
        series = {"^KQ11": s([1], ["2019-01-02"]), "X.KQ": s([1], ["2021-01-04"])}
        assert stress.source(row("X", 1), {"X": "X.KQ"}, series, "2020-01-02")[0] == "^KQ11"

    def test_hedged_etf_proxy_has_no_fx(self):
        series = {"^STOXX50E": s([1], ["2019-01-02"])}
        r = row("379790", 1, region="유럽", currency="KRW")
        assert stress.source(r, {}, series, "2020-01-02") == ("^STOXX50E", False, True)

    def test_unhedged_us_etf_listed_in_korea_does_not_convert_its_own_krw_price(self):
        r = row("133690", 1, region="미국", currency="USD")
        series = {"133690.KS": s([1], ["2019-01-02"])}
        assert stress.source(r, {"133690": "133690.KS"}, series, "2020-01-02") \
            == ("133690.KS", False, False)

    def test_cash(self):
        assert stress.source(row("", 1, asset="현금"), {}, SERIES, "2020-01-02") == (None, False, False)
        usd = row("", 1, asset="현금", region="미국", currency="USD")
        assert stress.source(usd, {}, SERIES, "2020-01-02") == (None, True, False)

    def test_no_proxy_for_region_is_excluded(self):
        r = row("241180", 1, region="일본", currency="기타 외화")
        assert stress.source(r, {}, SERIES, "2020-01-02") is None


class TestReplay:
    def test_joint_move_in_krw(self):
        rows = [row("005930", 100), row("AMZN", 100, region="미국", currency="USD", account="b")]
        tickers = {"005930": "005930.KS", "AMZN": "AMZN"}
        out = stress.replay(rows, tickers, SERIES, "2020-01-02", "2020-01-07")
        # KR: 0.90, 0.80, 0.85; US in KRW: 0.9*1.1=0.99, 0.8*1.2=0.96, 0.9*1.1=0.99
        assert out["ret"] == pytest.approx((85 + 99) / 200 - 1)
        assert out["worst"] == pytest.approx((80 + 96) / 200 - 1)
        assert out["worst_date"] == "2020-01-06"
        assert out["accounts"]["a"]["ret"] == pytest.approx(-0.15)
        assert out["accounts"]["b"]["worst"] == pytest.approx(-0.04)
        assert out["losers"][0] == ("005930", pytest.approx(-15))

    def test_usd_cash_moves_with_rate_and_krw_cash_does_not(self):
        rows = [row("", 100, asset="현금"), row("", 100, asset="현금", region="미국", currency="USD")]
        out = stress.replay(rows, {}, SERIES | {"^KS11": SERIES["^KS11"]}, "2020-01-02", "2020-01-07")
        # cash alone has no calendar of its own
        assert out == {}
        rows.append(row("005930", 0.0001))
        out = stress.replay(rows, {"005930": "005930.KS"}, SERIES, "2020-01-02", "2020-01-07")
        assert out["ret"] == pytest.approx(0.05, abs=1e-5)
        assert out["losers"] == [("005930", pytest.approx(-0.000015))]

    def test_excluded_share_and_proxied(self):
        rows = [row("X", 100), row("241180", 100, region="일본", currency="기타 외화")]
        out = stress.replay(rows, {}, SERIES, "2020-01-02", "2020-01-07")
        assert out["excluded_share"] == pytest.approx(0.5)
        assert out["proxied"] == ["X"]
        assert out["ret"] == pytest.approx(0.0)

    def test_worst_never_positive(self):
        series = {"U": s([1, 1.1, 1.2, 1.3])}
        out = stress.replay([row("U", 1)], {"U": "U"}, series, "2020-01-02", "2020-01-07")
        assert out["worst"] == 0.0


class TestHoldingRange:
    def test_buy_and_hold_from_todays_weights(self):
        days = [f"2020-01-{d:02d}" for d in range(1, 8)]
        series = {"A": s([1, 2, 1, 2, 1, 2, 1], days), "B": s([1] * 7, days)}
        rows = [row("A", 50), row("B", 50)]
        out = stress.holding_range(rows, {"A": "A", "B": "B"}, series, "2020-01-01", horizons=(1, 2))
        # 1-day windows: A alternates +100% / -50%, B flat
        assert sorted([out[1]["p5"], out[1]["p50"]]) == [pytest.approx(-0.25), pytest.approx(0.125)]
        assert out[1]["n"] == 6
        assert out[2]["p50"] == pytest.approx(0.0)
        assert out[2]["p_below_20"] == 0.0

    def test_horizon_longer_than_history_is_missing(self):
        out = stress.holding_range([row("A", 1)], {"A": "A"}, {"A": s([1, 2], DAYS[:2])},
                                   "2020-01-01", horizons=(5,))
        assert 5 not in out
