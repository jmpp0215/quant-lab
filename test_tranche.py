"""Tests for tranche bookkeeping.

The books decide how many shares each rebalance trades, so an error here
shows up as real money moving the wrong way.
"""

from decimal import Decimal

import pytest

import config
import tranche


class TestSplitEvenly:
    def test_divides_exactly_when_divisible(self):
        assert tranche.split_evenly(189, 3) == [63, 63, 63]

    def test_gives_remainder_to_earliest_tranches(self):
        assert tranche.split_evenly(26, 3) == [9, 9, 8]
        assert tranche.split_evenly(16, 3) == [6, 5, 5]

    def test_shares_always_sum_to_the_whole(self):
        # Losing or inventing a share here would silently desync the books
        # from the account.
        for quantity in range(200):
            assert sum(tranche.split_evenly(quantity, 3)) == quantity

    def test_handles_fewer_shares_than_tranches(self):
        assert tranche.split_evenly(2, 3) == [1, 1, 0]
        assert tranche.split_evenly(0, 3) == [0, 0, 0]


class TestInitialSplit:
    def test_assigns_every_share(self):
        actual = {"102110": 26, "091170": 189, "133690": 16}
        split = tranche.initial_split(actual)
        assert tranche.booked_total(split) == actual

    def test_omits_zero_allocations(self):
        split = tranche.initial_split({"102110": 2})
        assert "102110" not in split[config.TRANCHES[-1]]

    def test_covers_every_configured_tranche(self):
        split = tranche.initial_split({"102110": 26})
        assert set(split) == set(config.TRANCHES)


class TestReconcile:
    def test_matching_books_report_no_drift(self):
        actual = {"102110": 26, "091170": 189}
        assert tranche.reconcile(tranche.initial_split(actual), actual) == {}

    def test_reports_shares_the_books_do_not_know_about(self):
        books = tranche.initial_split({"102110": 26})
        drift = tranche.reconcile(books, {"102110": 30})
        assert drift == {"102110": 4}

    def test_reports_missing_shares_as_negative(self):
        books = tranche.initial_split({"102110": 26})
        drift = tranche.reconcile(books, {"102110": 20})
        assert drift == {"102110": -6}

    def test_reports_a_symbol_absent_from_the_account(self):
        books = tranche.initial_split({"102110": 26})
        assert tranche.reconcile(books, {}) == {"102110": -26}


class TestCashShare:
    def test_divides_the_pool_by_tranche_count(self):
        share = tranche.cash_share(Decimal("3000000"))
        assert share * len(config.TRANCHES) == Decimal("3000000")



class TestTradingDayIndex:
    def test_finds_position_within_the_month(self):
        candles = [{"timestamp": f"2026-09-{d:02d}T00:00:00.000+09:00"}
                   for d in (1, 2, 3, 4, 7, 8)]
        assert tranche.trading_day_index(candles, "2026-09-01") == 0
        assert tranche.trading_day_index(candles, "2026-09-07") == 4

    def test_ignores_other_months(self):
        candles = [{"timestamp": "2026-08-31T00:00:00.000+09:00"},
                   {"timestamp": "2026-09-01T00:00:00.000+09:00"}]
        assert tranche.trading_day_index(candles, "2026-09-01") == 0

    def test_returns_none_for_a_non_trading_day(self):
        candles = [{"timestamp": "2026-09-01T00:00:00.000+09:00"}]
        assert tranche.trading_day_index(candles, "2026-09-05") is None


class TestDueToday:
    def test_runs_a_tranche_on_its_scheduled_day(self):
        assert tranche.due_today(0, set()) == 0
        assert tranche.due_today(5, {0}) == 5

    def test_skips_a_tranche_already_done(self):
        assert tranche.due_today(0, {0}) is None

    def test_catches_up_a_missed_tranche(self):
        # Day 3 is past tranche 0's slot and it has not run: do it now
        # rather than waiting for next month.
        assert tranche.due_today(3, set()) == 0

    def test_handles_several_missed_tranches_oldest_first(self):
        assert tranche.due_today(12, set()) == 0
        assert tranche.due_today(12, {0}) == 5
        assert tranche.due_today(12, {0, 5}) == 10

    def test_returns_none_when_all_are_done(self):
        assert tranche.due_today(14, {0, 5, 10}) is None