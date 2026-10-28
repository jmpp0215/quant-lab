"""Tests for the tracker's upcoming-rebalance and dividend projections."""

from decimal import Decimal

from quant import config, schedule


def D(x) -> Decimal:
    return Decimal(str(x))


def month(prefix: str, n: int) -> list[str]:
    return [f"{prefix}-{d:02d}" for d in range(1, n + 1)]


class TestOpenDays:
    def test_keeps_open_days_across_responses_deduped(self):
        a = {"output": [{"bass_dt": "20261023", "opnd_yn": "Y"},
                        {"bass_dt": "20261024", "opnd_yn": "N"}]}
        b = {"output": [{"bass_dt": "20261023", "opnd_yn": "Y"},
                        {"bass_dt": "20261026", "opnd_yn": "Y"}]}

        assert schedule.open_days([b, a, {}]) == ["2026-10-23", "2026-10-26"]


class TestTrancheDates:
    def setup_method(self):
        self.tranches = config.TRANCHES
        config.TRANCHES = (0, 5, 10)

    def teardown_method(self):
        config.TRANCHES = self.tranches

    def test_schedules_nth_trading_day_of_each_month(self):
        days = month("2026-10", 20) + month("2026-11", 20)

        assert schedule.tranche_dates(days, "2026-10-01", set()) == [
            ("2026-10-01", 0), ("2026-10-06", 5), ("2026-10-11", 10),
            ("2026-11-01", 0), ("2026-11-06", 5), ("2026-11-11", 10)]

    def test_done_tranches_this_month_are_skipped(self):
        days = month("2026-10", 20) + month("2026-11", 3)

        assert schedule.tranche_dates(days, "2026-10-08", {0, 5}) == [
            ("2026-10-11", 10), ("2026-11-01", 0)]

    def test_missed_tranche_catches_up_on_next_open_day(self):
        days = month("2026-10", 20)

        assert schedule.tranche_dates(days, "2026-10-08", {0}) == [
            ("2026-10-08", 5), ("2026-10-11", 10)]

    def test_month_cut_off_by_calendar_loses_only_later_tranches(self):
        days = month("2026-10", 20) + month("2026-11", 7)

        assert schedule.tranche_dates(days, "2026-10-20", {0, 5, 10}) == [
            ("2026-11-01", 0), ("2026-11-06", 5)]


class TestTradingDaysAfter:
    def test_counts_trading_days(self):
        assert schedule.trading_days_after(month("2026-10", 25), "2026-10-02", 20) \
            == "2026-10-22"

    def test_start_outside_calendar(self):
        assert schedule.trading_days_after(month("2026-10", 5), "2026-09-30", 1) is None

    def test_beyond_calendar(self):
        assert schedule.trading_days_after(month("2026-10", 5), "2026-10-01", 9) is None


class TestUpcomingDividends:
    HOLD = [{"account": "toss-bot", "symbol": "A", "name": "에이", "qty": 10},
            {"account": "kis-isa", "symbol": "A", "name": "에이", "qty": 3},
            {"account": "kis-isa", "symbol": "B", "name": "비", "qty": 5}]

    def test_one_row_per_account_holding_with_expected_amount(self):
        events = [{"symbol": "A", "record_date": "2026-10-31",
                   "amount": D(100), "pay_date": "2026-11-04"}]

        rows = schedule.upcoming_dividends(events, self.HOLD, "2026-10-23")

        assert [(r["account"], r["expected"]) for r in rows] == [
            ("kis-isa", D(300)), ("toss-bot", D(1000))]

    def test_unset_amount_has_no_expected(self):
        events = [{"symbol": "B", "record_date": "2026-12-31", "amount": D(0),
                   "pay_date": None}]

        [row] = schedule.upcoming_dividends(events, self.HOLD, "2026-10-23")

        assert (row["per_share"], row["expected"]) == (None, None)

    def test_past_and_unheld_events_are_dropped(self):
        events = [{"symbol": "A", "record_date": "2026-09-30", "amount": D(1),
                   "pay_date": None},
                  {"symbol": "Z", "record_date": "2026-12-31", "amount": D(1),
                   "pay_date": None}]

        assert schedule.upcoming_dividends(events, self.HOLD, "2026-10-23") == []


class TestParseEarnings:
    def test_expected_after_close(self):
        text = ("Apple Inc. Common Stock is expected* to report earnings on  "
                "11/02/2026 after market close.  The report will be for ...")

        assert schedule.parse_earnings(text) == {
            "us_date": "2026-11-02", "timing": "amc", "estimated": False}

    def test_before_open(self):
        text = "X is expected* to report earnings on  01/27/2027 before market open."

        assert schedule.parse_earnings(text)["timing"] == "bmo"

    def test_algorithmic_estimate_without_time(self):
        text = ("NVIDIA Corporation Common Stock is estimated to report earnings on  "
                "11/18/2026. The upcoming earnings date is derived from an algorithm")

        assert schedule.parse_earnings(text) == {
            "us_date": "2026-11-18", "timing": None, "estimated": True}

    def test_no_date(self):
        text = ("Our vendor, Zacks Investment Research, hasn't provided us with "
                "the upcoming earnings report date.")

        assert schedule.parse_earnings(text) is None
        assert schedule.parse_earnings(None) is None


class TestEarningsKst:
    def test_after_close_in_edt_is_5am_next_day(self):
        assert schedule.earnings_kst("2026-10-29", "amc") == ("2026-10-30", "05:00")

    def test_after_close_in_est_is_6am_next_day(self):
        # US DST ends 2026-11-01.
        assert schedule.earnings_kst("2026-11-02", "amc") == ("2026-11-03", "06:00")

    def test_before_open_is_same_evening(self):
        assert schedule.earnings_kst("2026-10-29", "bmo") == ("2026-10-29", "21:00")
        assert schedule.earnings_kst("2026-11-03", "bmo") == ("2026-11-03", "22:00")

    def test_unknown_time_keeps_us_date(self):
        assert schedule.earnings_kst("2026-11-18", None) == ("2026-11-18", None)


class TestMergeEarnings:
    AMZN = {"us_date": "2026-10-29", "timing": "amc", "estimated": False}
    MSFT = {"us_date": "2026-10-28", "timing": "amc", "estimated": False}

    def test_fetch_wins_over_cache(self):
        moved = dict(self.AMZN, us_date="2026-10-30")

        out = schedule.merge_earnings({"AMZN": self.AMZN}, {"AMZN": moved},
                                      {"AMZN"}, "2026-10-28")

        assert out == {"AMZN": moved}

    def test_keeps_cached_date_nasdaq_blanked_on_report_day(self):
        # MSFT reports 10/28 after the US close = 10/29 05:00 KST.
        out = schedule.merge_earnings({"MSFT": self.MSFT}, {}, {"MSFT"}, "2026-10-29")

        assert out == {"MSFT": self.MSFT}

    def test_drops_cached_date_once_past_in_kst(self):
        out = schedule.merge_earnings({"MSFT": self.MSFT}, {}, {"MSFT"}, "2026-10-30")

        assert out == {}

    def test_drops_symbols_no_longer_held(self):
        out = schedule.merge_earnings({"AMZN": self.AMZN}, {"MSFT": self.MSFT},
                                      {"MSFT"}, "2026-10-28")

        assert out == {"MSFT": self.MSFT}
