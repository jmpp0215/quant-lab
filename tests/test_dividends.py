"""Tests for the dividend-event incremental cache."""

from datetime import date
from decimal import Decimal

from quant import dividends, storage
from quant.kis_client import KisApiError


class FakeKisClient:
    """Records every ksdinfo/dividend call and returns canned rows."""

    def __init__(self, rows_by_symbol: dict[str, list[dict]] | None = None):
        self.rows_by_symbol = rows_by_symbol or {}
        self.calls: list[dict] = []

    def get(self, path, tr_id, params=None):
        self.calls.append(dict(params or {}))
        symbol = params["SHT_CD"]
        return {"output1": self.rows_by_symbol.get(symbol, [])}


class FailingKisClient:
    def get(self, path, tr_id, params=None):
        raise KisApiError(500, "1", "EGW00000", "boom")


def row(record_date: str, amount: str) -> dict:
    return {"record_date": record_date, "per_sto_divi_amt": amount}


class TestSync:
    def test_first_sync_backfills_the_full_window(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        client = FakeKisClient({"379790": [row("20260731", "210")]})
        today = date(2026, 9, 1)

        with storage.connect(db) as conn:
            dividends.sync(conn, client, "379790", today=today)
            events = storage.load_dividend_events(conn, "379790")

        assert events == [{"record_date": "2026-07-31",
                          "amount": Decimal("210")}]
        [call] = client.calls
        f_dt = call["F_DT"]
        from_date = date(int(f_dt[:4]), int(f_dt[4:6]), int(f_dt[6:]))
        # First-ever sync must reach back the full BACKFILL_MONTHS window,
        # not just a short incremental slice.
        assert (today - from_date).days > 365 * 2
        assert call["T_DT"] == "20260901"

    def test_second_sync_same_day_makes_no_api_call(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        client = FakeKisClient({"379790": [row("20260731", "210")]})
        today = date(2026, 9, 1)

        with storage.connect(db) as conn:
            dividends.sync(conn, client, "379790", today=today)
            dividends.sync(conn, client, "379790", today=today)

        assert len(client.calls) == 1

    def test_stale_sync_requeries_from_overlap_window(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        client = FakeKisClient({"379790": []})
        day1 = date(2026, 9, 1)
        day2 = date(2026, 9, 5)

        with storage.connect(db) as conn:
            dividends.sync(conn, client, "379790", today=day1)
            dividends.sync(conn, client, "379790", today=day2)

        assert len(client.calls) == 2
        second_from = client.calls[1]["F_DT"]
        expected = (day1 - dividends.timedelta(
            days=dividends.OVERLAP_DAYS)).strftime("%Y%m%d")
        assert second_from == expected
        assert client.calls[1]["T_DT"] == "20260905"

    def test_sync_all_continues_past_one_symbols_failure(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        good = FakeKisClient({"379790": [row("20260731", "210")]})
        today = date(2026, 9, 1)

        with storage.connect(db) as conn:
            # First sync one symbol with a failing client (records nothing,
            # but must not raise out of sync_all).
            dividends.sync_all(conn, FailingKisClient(), ["379790"],
                              today=today)
            assert storage.dividend_fetch_state(conn, "379790") is None

            # A second symbol on a working client in the same sync_all call
            # must still be processed.
            dividends.sync_all(conn, good, ["379790", "133690"],
                              today=today)
            events = storage.load_all_dividend_events(
                conn, ["379790", "133690"])

        assert events["379790"] == [
            {"record_date": "2026-07-31", "amount": Decimal("210")}]
        assert events["133690"] == []
