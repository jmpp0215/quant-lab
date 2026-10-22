"""Tests for daily.py's unattended account health check."""

from decimal import Decimal

import daily
from daily import check_account_health
from quant import accounts, storage


def snapshot(positions, cash="100000", currency="KRW"):
    return storage.AccountSnapshot(account="test-acc", currency=currency,
                                   total=Decimal(cash), cash=Decimal(cash),
                                   positions=positions)


class TestCheckAccountHealth:
    def test_clean_account_reports_nothing(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        with storage.connect(db) as conn:
            problems = check_account_health(
                conn, "test-acc", snapshot(positions=[]), "2026-08-24")
        assert problems == []

    def test_matching_tranche_books_report_no_drift(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        with storage.connect(db) as conn:
            storage.save_tranche_holdings(
                conn, 0, "test-acc", {"102110": 10}, "2026-08-24T00:00:00")
            problems = check_account_health(
                conn, "test-acc",
                snapshot(positions=[{"symbol": "102110", "qty": 10}]),
                "2026-08-24")
        assert problems == []

    def test_mismatched_tranche_books_report_drift(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        with storage.connect(db) as conn:
            storage.save_tranche_holdings(
                conn, 0, "test-acc", {"102110": 10}, "2026-08-24T00:00:00")
            # Account shows 7, not the 10 the books say - something
            # traded outside this system.
            problems = check_account_health(
                conn, "test-acc",
                snapshot(positions=[{"symbol": "102110", "qty": 7}]),
                "2026-08-24")
        assert len(problems) == 1
        assert "102110" in problems[0]

    def test_accounts_without_tranches_skip_drift_check(self, tmp_path):
        # kis-main-overseas and similar accounts never get tranche books -
        # reconcile would otherwise flag every position as "unexplained".
        db = tmp_path / "test.db"
        storage.init(db)
        with storage.connect(db) as conn:
            problems = check_account_health(
                conn, "test-acc",
                snapshot(positions=[{"symbol": "AAPL", "qty": 5}]),
                "2026-08-24")
        assert problems == []

    @staticmethod
    def _history(db, account, cashes, positions=()):
        # daily.py saves today's portfolio row before calling the check.
        with storage.connect(db) as conn:
            for i, cash in enumerate(cashes):
                storage.save_portfolio(conn, f"2026-08-{20 + i:02d}", account, "KRW",
                                       Decimal(cash), Decimal(cash), list(positions))

    def _check(self, db, account="test-acc", positions=()):
        with storage.connect(db) as conn:
            return check_account_health(conn, account, snapshot(positions=list(positions)),
                                        "2026-08-29")

    def test_confirmed_unrecorded_withdrawal_is_reported(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        self._history(db, "test-acc", ["500000", "150000", "150000", "150000", "150000"])
        problems = self._check(db)
        assert any("unrecorded transfer" in p and "-350,000" in p for p in problems)

    def test_change_too_recent_to_confirm_is_not_reported_yet(self, tmp_path):
        # Settlement can undo it within the next snapshots, so wait.
        db = tmp_path / "test.db"
        storage.init(db)
        self._history(db, "test-acc", ["500000", "500000", "150000"])
        assert self._check(db) == []

    def test_small_cash_wobble_is_not_reported(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        self._history(db, "test-acc", ["500000", "500500", "500500", "500500", "500500"])
        assert self._check(db) == []

    def test_recorded_deposit_explains_a_cash_increase(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        self._history(db, "test-acc", ["500000", "1500000", "1500000", "1500000", "1500000"])
        with storage.connect(db) as conn:
            storage.save_cashflow(conn, "2026-08-21", "test-acc", Decimal("1000000"),
                                  note="deposit")
        assert self._check(db) == []

    def test_dismissed_suspect_is_not_reported_again(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        self._history(db, "test-acc", ["500000", "150000", "150000", "150000", "150000"])
        with storage.connect(db) as conn:
            storage.save_transfer_review(conn, "test-acc", "2026-08-21",
                                         Decimal("-350000"), "2026-08-29T00:00:00")
        assert self._check(db) == []

    def test_overseas_rate_move_is_not_reported(self, tmp_path):
        # kis-main-overseas: USD cash and positions valued in KRW, so the
        # exchange rate alone moves the KRW cash every day (+30,047 / -6,010
        # KRW on 2026-09-29/30 under the old check).
        db = tmp_path / "test.db"
        storage.init(db)
        with storage.connect(db) as conn:
            for i, rate in enumerate(["1393", "1328", "1340", "1345", "1350"]):
                r = Decimal(rate)
                storage.save_portfolio(
                    conn, f"2026-08-{20 + i:02d}", "kis-main-overseas", "KRW",
                    (Decimal("3758.47") + 2500) * r, Decimal("3758.47") * r,
                    [{"symbol": "AMZN", "qty": 10, "price": "250", "currency": "USD"}])
        assert self._check(db, "kis-main-overseas") == []


class TestHealthcheckProblem:
    def test_fresh_heartbeat_is_fine(self, tmp_path):
        from daily import healthcheck_problem
        hb = tmp_path / "hb"
        hb.write_text("1000\n")
        assert healthcheck_problem(hb, now=1000 + 3600) is None

    def test_stale_heartbeat_is_reported(self, tmp_path):
        # 15:40 daily vs a heartbeat last stamped 16:30 the day before:
        # today's 13:15 check never ran.
        from daily import healthcheck_problem
        hb = tmp_path / "hb"
        hb.write_text("1000\n")
        msg = healthcheck_problem(hb, now=1000 + 23 * 3600)
        assert msg is not None and "23.0h" in msg

    def test_missing_heartbeat_is_not_an_alert(self, tmp_path):
        # Before the watchdog is installed there is no file at all.
        from daily import healthcheck_problem
        assert healthcheck_problem(tmp_path / "absent", now=0) is None

    def test_garbage_heartbeat_is_reported(self, tmp_path):
        from daily import healthcheck_problem
        hb = tmp_path / "hb"
        hb.write_text("not a number")
        assert "unreadable" in healthcheck_problem(hb, now=0)


class TestAccountsInSlot:
    def test_toss_bot_is_snapshotted_in_the_morning_only(self):
        assert daily.accounts_in_slot(accounts.ACCOUNTS, "morning") == ["toss-bot"]
        assert "toss-bot" not in daily.accounts_in_slot(accounts.ACCOUNTS, "close")

    def test_every_account_belongs_to_exactly_one_slot(self):
        morning = daily.accounts_in_slot(accounts.ACCOUNTS, "morning")
        close = daily.accounts_in_slot(accounts.ACCOUNTS, "close")
        assert sorted(morning + close) == sorted(accounts.ACCOUNTS)

    def test_strategy_account_stays_in_the_close_run(self):
        # The signal is recorded only alongside the strategy account's
        # snapshot; moving it to the morning would record it pre-open.
        close = daily.accounts_in_slot(accounts.ACCOUNTS, "close")
        assert all(name in close for name, cfg in accounts.ACCOUNTS.items()
                   if cfg["strategy"])

    def test_missing_slot_means_close(self):
        registry = {"a": {}, "b": {"snapshot_slot": "morning"}}
        assert daily.accounts_in_slot(registry, "close") == ["a"]
        assert daily.accounts_in_slot(registry, "morning") == ["b"]


class TestTranchePreview:
    # Real universe codes so config lookups behave as in production; prices
    # are multiples of the 5-won ETF tick.
    A, B = "102110", "091170"
    PRICES = {A: Decimal("100000"), B: Decimal("10000")}

    def setup_method(self):
        from quant import config
        self.config, self.tranches = config, config.TRANCHES
        config.TRANCHES = (0, 5, 10)

    def teardown_method(self):
        self.config.TRANCHES = self.tranches

    def test_sizes_to_a_third_of_the_account_and_plans_orders(self):
        # Sleeve 5 holds 10 x A (1,000,000); others hold 10 x A each; cash
        # 600,000 -> account 3,600,000, target 1,200,000, all into B.
        books = {0: {self.A: 10}, 5: {self.A: 10}, 10: {self.A: 10}}
        p = daily.tranche_preview({self.B: Decimal("1")}, books, 5, self.PRICES,
                                  Decimal("600000"), {self.A: 30})

        assert (p["tranche"], p["tranches"], p["drift"]) == (2, 3, None)
        assert Decimal(p["target"]) == Decimal("1200000")
        assert [(o["side"], o["symbol"], o["qty"]) for o in p["orders"]] == [
            ("SELL", self.A, 10), ("BUY", self.B, 120)]

    def test_reports_drift_without_hiding_the_plan(self):
        books = {0: {self.A: 10}}
        p = daily.tranche_preview({self.A: Decimal("1")}, books, 0, self.PRICES,
                                  Decimal("0"), {self.A: 9})

        assert p["drift"] == {self.A: -1}

    def test_short_account_reports_deposit_needed(self):
        books = {0: {}, 5: {self.A: 20}, 10: {}}
        p = daily.tranche_preview({self.B: Decimal("1")}, books, 0, self.PRICES,
                                  Decimal("100000"), {self.A: 20})

        # account 2,100,000 -> target 700,000; reachable only the 100,000 cash
        assert Decimal(p["value"]) == Decimal("100000")
        assert Decimal(p["deposit_needed"]) > 0
