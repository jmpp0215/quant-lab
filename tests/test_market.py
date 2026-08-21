"""Tests for market session and tick size logic."""

from decimal import Decimal

import pytest

from quant import config, market


class TestTickSize:
    @pytest.mark.parametrize("price,expected", [
        ("500", "1"),
        ("1500", "1"),
        ("3000", "5"),
        ("7000", "10"),
        ("15000", "10"),
        ("30000", "50"),
        ("70000", "100"),
        ("150000", "100"),
        ("241000", "500"),
        ("600000", "1000"),
    ])
    def test_returns_correct_tick_for_price_band(self, price, expected):
        assert market.kr_tick_size(price) == Decimal(expected)

    def test_boundary_belongs_to_upper_band(self):
        # 2000 is the start of the 5-won band, not the end of the 1-won one.
        assert market.kr_tick_size("1999") == Decimal("1")
        assert market.kr_tick_size("2000") == Decimal("5")
class TestEtfTickSize:
    @pytest.mark.parametrize("price", ["7275", "16175", "109690", "1074950"])
    def test_etf_ticks_are_flat_five_won(self, price):
        assert market.kr_tick_size(price, is_etf=True) == Decimal("5")

    def test_etf_validation_accepts_five_won_steps(self):
        # A share at this price would step by 1,000 won; an ETF does not.
        assert market.is_valid_kr_price("1074955", is_etf=True)
        assert not market.is_valid_kr_price("1074955")

class TestPriceValidation:
    def test_accepts_price_on_tick(self):
        assert market.is_valid_kr_price("241000")
        assert market.is_valid_kr_price("15760")

    def test_rejects_price_off_tick(self):
        assert not market.is_valid_kr_price("241001")
        assert not market.is_valid_kr_price("15765")

    def test_accepts_decimal_input(self):
        assert market.is_valid_kr_price(Decimal("241000"))


class TestRoundToTick:
    def test_rounds_down_to_nearest_tick(self):
        assert market.round_to_tick(Decimal("241037")) == Decimal("241000")
        assert market.round_to_tick(Decimal("15767")) == Decimal("15760")

    def test_leaves_valid_price_unchanged(self):
        assert market.round_to_tick(Decimal("241000")) == Decimal("241000")

    def test_result_is_always_valid(self):
        for raw in ("1537", "48037", "163537", "241037", "600037"):
            snapped = market.round_to_tick(Decimal(raw))
            assert market.is_valid_kr_price(snapped)

class TestBusinessDay:
    def test_holiday_has_no_sessions(self):
        calendar = {"result": {"today": {"date": "2026-08-17",
                                         "integrated": None}}}
        assert not market.is_business_day(calendar)
        assert market.current_session(calendar) is None

    def test_trading_day_has_sessions(self):
        calendar = {"result": {"today": {"date": "2026-08-14", "integrated": {
            "regularMarket": {
                "startTime": "2026-08-14T09:00:00.000+09:00",
                "endTime": "2026-08-14T15:30:00.000+09:00",
            }}}}}
        assert market.is_business_day(calendar)


class TestEtfClassification:
    """is_etf() decides which tick grid a symbol prices on, so a wrong
    answer either refuses a valid order or places one on the wrong grid.

    Every tick below was measured from the live KIS order book on
    2026-08-21 by reading the spacing between consecutive quote levels,
    not taken from a rule table.
    """

    # (symbol, price, expected is_etf, tick observed in the live book)
    HOLDINGS = [
        ("005380", "418500", False, "500"),   # 현대차
        ("005490", "313500", False, "500"),   # POSCO홀딩스
        ("0074K0", "16955", True, "5"),       # KoAct - ETF, not in UNIVERSE
        ("0167Z0", "8140", True, "5"),        # KODEX 미국우주항공
        ("058470", "65400", False, "100"),    # 리노공업
        ("105560", "163100", False, "100"),   # KB금융
    ]

    @pytest.mark.parametrize("symbol,price,is_etf,tick", HOLDINGS)
    def test_matches_the_live_order_book(self, symbol, price, is_etf, tick):
        assert config.is_etf(symbol) is is_etf
        assert market.kr_tick_size(price, is_etf=is_etf) == Decimal(tick)
        assert market.is_valid_kr_price(price, is_etf=is_etf)

    def test_held_etf_outside_the_universe_still_prices_as_an_etf(self):
        # The regression this exists for: 0074K0 is a real ETF that is in
        # neither UNIVERSE nor WATCH_ONLY. While is_etf() was membership
        # based it got the 10-won stock grid, which refused its real
        # market price as off-tick about half the time and silently
        # priced a tick too far through the touch the other half.
        assert "0074K0" not in config.UNIVERSE
        assert "0074K0" not in config.WATCH_ONLY
        assert config.is_etf("0074K0")

    def test_held_etfs_are_not_traded_as_candidates(self):
        # HELD_ETFS exists only to answer the tick question. Leaking into
        # all_symbols() would start fetching candles for it and let the
        # strategy rank it.
        assert "0074K0" not in config.all_symbols()

    def test_an_unknown_symbol_is_not_an_etf(self):
        assert not config.is_etf("005930")   # 삼성전자, never held
