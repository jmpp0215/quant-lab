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
