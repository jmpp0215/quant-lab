"""Tests for the tracker's one write path: recording a cashflow."""

from decimal import Decimal

import pytest

import tracker_server
from quant import storage


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "quant.db"
    storage.init(path)
    return path


def rows(db):
    with storage.connect(db) as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT trade_date, account, amount, kind, note FROM cashflows")]


BODY = {"account": "kis-isa", "date": "2026-11-03", "amount": 1000000,
        "note": "월 적립"}


class TestRecordCashflow:
    def test_records_a_deposit_row(self, db):
        assert tracker_server.record_cashflow(BODY, db) == (200, {"ok": True})
        assert rows(db) == [("2026-11-03", "kis-isa", "1000000", "deposit", "월 적립")]

    def test_withdrawal_is_negative(self, db):
        tracker_server.record_cashflow(dict(BODY, amount=-500000), db)
        assert Decimal(rows(db)[0][2]) == -500000

    def test_duplicate_refused_unless_forced(self, db):
        tracker_server.record_cashflow(BODY, db)

        status, payload = tracker_server.record_cashflow(BODY, db)
        assert status == 409 and payload["duplicate"]
        assert len(rows(db)) == 1

        assert tracker_server.record_cashflow(dict(BODY, force=True), db)[0] == 200
        assert len(rows(db)) == 2

    @pytest.mark.parametrize("bad", [
        {"account": "nope"}, {"date": "2026-13-01"}, {"amount": 0},
        {"amount": "12.5"}, {"amount": "abc"}])
    def test_invalid_input_raises_before_writing(self, db, bad):
        with pytest.raises((ValueError, ArithmeticError)):
            tracker_server.record_cashflow(dict(BODY, **bad), db)
        assert rows(db) == []


class TestDismissTransfer:
    def test_records_a_review_that_hides_the_suspect(self, db):
        with storage.connect(db) as conn:
            for d, cash in [("2026-08-20", "500000"), ("2026-08-21", "150000"),
                            ("2026-08-24", "150000"), ("2026-08-25", "150000"),
                            ("2026-08-26", "150000")]:
                storage.save_portfolio(conn, d, "kis-isa", "KRW", Decimal(cash),
                                       Decimal(cash), [])
            assert [s["date"] for s in storage.transfer_suspects(conn, "kis-isa")] == [
                "2026-08-21"]

        body = {"account": "kis-isa", "date": "2026-08-21", "amount": -350000}
        assert tracker_server.dismiss_transfer(body, db) == (200, {"ok": True})

        with storage.connect(db) as conn:
            assert storage.transfer_suspects(conn, "kis-isa") == []

    def test_unknown_account_raises(self, db):
        with pytest.raises(ValueError):
            tracker_server.dismiss_transfer(
                {"account": "nope", "date": "2026-08-21", "amount": 1}, db)


class TestSaveIsaSettings:
    def test_stores_validated_values_and_clears_empty_ones(self, db):
        body = {"isa_type": "서민형", "isa_open_date": "2025-03-01",
                "isa_paid_total": "13,510,023", "isa_annual_limit": "40000000",
                "isa_taxfree_limit": ""}
        assert tracker_server.save_isa_settings(body, db) == (200, {"ok": True})
        with storage.connect(db) as conn:
            s = storage.load_settings(conn)
        assert s["isa_paid_total"] == "13510023"
        assert s["isa_open_date"] == "2025-03-01"
        assert s["isa_taxfree_limit"] is None

    @pytest.mark.parametrize("bad", [{"nope": "1"}, {"isa_paid_total": "-1"},
                                     {"isa_paid_total": "1.5"}, {"isa_open_date": "2025-13-01"}])
    def test_rejects_bad_input(self, db, bad):
        with pytest.raises((ValueError, ArithmeticError)):
            tracker_server.save_isa_settings(bad, db)
