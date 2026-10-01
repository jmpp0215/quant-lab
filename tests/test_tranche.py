"""Tests for tranche bookkeeping.

The books decide how many shares each rebalance trades, so an error here
shows up as real money moving the wrong way.
"""

from decimal import Decimal

from quant import config, tranche


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


class TestSleeveBudget:
    """A rebalancing sleeve sizes to 1/N of the whole account - every
    sleeve's holdings plus the cash pool - capped at what it can reach
    (its own holdings plus all the cash), with the deposit that would
    close any gap reported. N = 3 throughout (config.TRANCHES)."""

    PRICES = {"102110": Decimal("100000"), "091170": Decimal("10000")}

    def test_equal_sleeves_target_a_third_of_the_account(self):
        books = {0: {"102110": 10}, 5: {"102110": 10}, 10: {"102110": 10}}
        b = tranche.sleeve_budget(books, 0, self.PRICES, Decimal("600000"))
        # 3,000,000 equity + 600,000 cash = 3,600,000; a third each.
        assert b.target == Decimal("1200000")
        assert b.value == b.target
        assert b.deposit_needed == 0

    def test_an_overweight_sleeve_targets_below_its_holdings(self):
        # 20 x 100,000 = 2,000,000 in sleeve 0 of a 3,000,000 account:
        # it sells down to 1,000,000 and the proceeds go to the pool.
        books = {0: {"102110": 20}, 5: {"102110": 5}, 10: {"102110": 5}}
        b = tranche.sleeve_budget(books, 0, self.PRICES, Decimal("0"))
        assert b.target == Decimal("1000000")
        assert b.value == b.target

    def test_an_empty_sleeve_in_a_cash_heavy_account_gets_a_full_third(self):
        # The old own-holdings + cash/N rule gave this sleeve only
        # 1,500,000 / 3 = 500,000 (the tranche-5 underfill of 2026-09).
        books = {0: {"102110": 15}, 5: {}, 10: {}}
        b = tranche.sleeve_budget(books, 5, self.PRICES, Decimal("1500000"))
        assert b.target == Decimal("1000000")
        assert b.value == b.target
        assert b.deposit_needed == 0

    def test_a_sleeve_missing_from_the_books_counts_as_empty(self):
        books = {0: {"102110": 15}}
        b = tranche.sleeve_budget(books, 5, self.PRICES, Decimal("1500000"))
        assert b.target == Decimal("1000000")

    def test_short_of_cash_sizes_to_what_it_can_reach(self):
        # Sleeves 5/10 hold 2,000,000 each, sleeve 0 holds 200,000, pool
        # 100,000: account 4,300,000, target 1,433,333; reachable only
        # 300,000.
        books = {0: {"091170": 20}, 5: {"102110": 20}, 10: {"102110": 20}}
        b = tranche.sleeve_budget(books, 0, self.PRICES, Decimal("100000"))
        assert b.value == Decimal("300000")
        assert b.value < b.target
        # The gap is 1,133,333; a deposit also lifts the target by 1/3 of
        # itself, so it takes 1.5x the gap.
        assert b.deposit_needed == (b.target - b.value) * 3 / 2

    def test_depositing_the_reported_amount_reaches_the_target(self):
        books = {0: {"091170": 20}, 5: {"102110": 20}, 10: {"102110": 20}}
        before = tranche.sleeve_budget(books, 0, self.PRICES,
                                       Decimal("100000"))
        after = tranche.sleeve_budget(books, 0, self.PRICES,
                                      Decimal("100000") + before.deposit_needed)
        # Within Decimal rounding of the 1/3 divisions.
        assert abs(after.target - after.value) < Decimal("0.01")
        assert after.deposit_needed < Decimal("0.01")

    def test_symbols_without_a_price_are_left_out(self):
        books = {0: {"102110": 10, "999999": 50}, 5: {}, 10: {}}
        b = tranche.sleeve_budget(books, 0, self.PRICES, Decimal("0"))
        assert b.target == Decimal("1000000") / 3

    def test_explicit_n_overrides_the_config_tranche_count(self):
        books = {0: {"102110": 10}}
        b = tranche.sleeve_budget(books, 0, self.PRICES, Decimal("0"), n=1)
        assert b.target == Decimal("1000000")
        assert b.deposit_needed == 0

    def test_kis_isa_tranche_5_after_the_2026_10_01_run(self):
        # Books and buying power as of 2026-10-01 12:50 after tranche 0.
        # Old rule: tranche 5 = 3,709,805 + 4,493,388/3 = 5,207,601. New:
        # a third of the 16,783,698 account.
        prices = {"102110": Decimal("110725"), "133690": Decimal("184475"),
                  "091170": Decimal("15725"), "379790": Decimal("16960")}
        books = {
            0: {"091170": 108, "102110": 15, "379790": 100},
            5: {"091170": 75, "102110": 11, "133690": 5, "379790": 23},
            10: {"091170": 75, "102110": 11, "133690": 4, "379790": 23},
        }
        b = tranche.sleeve_budget(books, 5, prices, Decimal("4493388"))
        assert b.target == Decimal("16783698") / 3
        assert b.value == b.target
        assert b.deposit_needed == 0



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
        
    def test_no_catch_up_before_the_start_month(self):
        # Sleeves that never had a scheduled day in the introduction month
        # wait rather than firing on consecutive days.
        assert tranche.due_today(12, set(), "2026-08") is None
        assert tranche.due_today(5, set(), "2026-08") == 5

    def test_catch_up_applies_from_the_start_month(self):
        assert tranche.due_today(12, set(), "2026-09") == 0