import pytest

from quant.common.total_return import ex_date, total_return_series

# Late-December 2024 KRX calendar: 12/31 closed, 12/25 holiday.
CAL = ["20241223", "20241224", "20241226", "20241227", "20241230",
       "20250102", "20250103"]


class TestExDate:
    def test_trading_day_record_date_goes_ex_the_day_before(self):
        assert ex_date("20241226", CAL) == "20241224"

    def test_closed_year_end_record_date_goes_ex_two_days_before(self):
        # 12/31 is not a settlement day: buys on 12/27 settle 1/2, so 12/27
        # is already ex - the 2024 year-end 배당락일 was indeed 12/27.
        assert ex_date("20241231", CAL) == "20241227"

    def test_record_date_on_holiday_mid_calendar(self):
        # 12/25 closed: last settlement day <= 12/25 is 12/24, ex 12/23.
        assert ex_date("20241225", CAL) == "20241223"

    def test_record_date_past_calendar_end_is_unplaceable(self):
        assert ex_date("20250110", CAL) is None

    def test_record_date_before_calendar_start_is_unplaceable(self):
        assert ex_date("20241223", CAL) is None


class TestTotalReturnSeries:
    def test_no_dividends_is_the_raw_series(self):
        prices = {"1": 100.0, "2": 110.0, "3": 99.0}
        assert total_return_series(prices, {}) == prices

    def test_dividend_offsets_the_ex_date_drop(self):
        # Closes 10,000 -> 9,650 on a 350 KRW ex-date: a holder lost
        # nothing, so the TR index is flat while the raw close fell 3.5%.
        tr = total_return_series({"1": 10000.0, "2": 9650.0, "3": 9843.0},
                                 {"2": 350.0})
        assert tr["2"] == pytest.approx(10000.0)
        assert tr["3"] == pytest.approx(10000.0 * 9843 / 9650)

    def test_dividend_on_a_halted_day_is_credited_next_priced_day(self):
        tr = total_return_series({"1": 1000.0, "3": 950.0}, {"2": 50.0})
        assert tr["3"] == pytest.approx(1000.0)

    def test_dividend_before_first_close_is_ignored(self):
        prices = {"2": 1000.0, "3": 1010.0}
        assert total_return_series(prices, {"1": 50.0, "2": 50.0}) == prices

    def test_dividends_compound(self):
        # Flat 1,000 raw close, 10 KRW ex on day 2 and day 3: each reinvests.
        tr = total_return_series({"1": 1000.0, "2": 990.0, "3": 980.0},
                                 {"2": 10.0, "3": 10.0})
        assert tr["3"] == pytest.approx(1000.0)
        tr2 = total_return_series({"1": 1000.0, "2": 1000.0, "3": 1000.0},
                                  {"2": 10.0, "3": 10.0})
        assert tr2["3"] == pytest.approx(1000.0 * 1.01 * 1.01)
