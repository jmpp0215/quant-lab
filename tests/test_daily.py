"""Tests for daily.py's unattended account health check."""

from decimal import Decimal

from daily import check_account_health
from quant import storage


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

    def test_large_unexplained_cash_drop_is_reported(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        with storage.connect(db) as conn:
            storage.save_portfolio(conn, "2026-08-21", "test-acc", "KRW",
                                   Decimal("500000"), Decimal("500000"), [])
        # daily.py saves today's portfolio row before calling this, so
        # unexplained_cash_change has both rows to diff.
        with storage.connect(db) as conn:
            storage.save_portfolio(conn, "2026-08-24", "test-acc", "KRW",
                                   Decimal("150000"), Decimal("150000"), [])
            problems = check_account_health(
                conn, "test-acc", snapshot(positions=[], cash="150000"),
                "2026-08-24")
        assert any("unexplained cash" in p for p in problems)

    def test_small_cash_wobble_is_not_reported(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        with storage.connect(db) as conn:
            storage.save_portfolio(conn, "2026-08-21", "test-acc", "KRW",
                                   Decimal("500000"), Decimal("500000"), [])
            storage.save_portfolio(conn, "2026-08-24", "test-acc", "KRW",
                                   Decimal("500500"), Decimal("500500"), [])
            problems = check_account_health(
                conn, "test-acc", snapshot(positions=[], cash="500500"),
                "2026-08-24")
        assert problems == []

    def test_deposit_explains_a_cash_increase(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        with storage.connect(db) as conn:
            storage.save_portfolio(conn, "2026-08-21", "test-acc", "KRW",
                                   Decimal("500000"), Decimal("500000"), [])
            storage.save_cashflow(conn, "2026-08-24", "test-acc",
                                  Decimal("1000000"), note="deposit")
            storage.save_portfolio(conn, "2026-08-24", "test-acc", "KRW",
                                   Decimal("1500000"), Decimal("1500000"), [])
            problems = check_account_health(
                conn, "test-acc", snapshot(positions=[], cash="1500000"),
                "2026-08-24")
        assert problems == []
