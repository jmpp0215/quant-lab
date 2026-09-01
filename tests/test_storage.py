"""Tests for storage.py's persistence contracts."""

from decimal import Decimal

from quant import storage


class TestSaveOrder:
    def test_executed_date_round_trips(self, tmp_path):
        # Regression: save_order accepted executed_date as a parameter but
        # never included it in the INSERT, so every order silently
        # recorded NULL - which unexplained_cash_change filters on, so it
        # could never actually match anything for a real order.
        db = tmp_path / "test.db"
        storage.init(db)
        with storage.connect(db) as conn:
            storage.save_order(
                conn, "2026-08-24", "2026-08-24T10:00:00", "test-acc",
                "102110", "BUY", 10, Decimal("109620"), "order-1",
                filled=True, executed_date="2026-08-25",
            )
            row = conn.execute(
                "SELECT executed_date FROM orders WHERE order_id = ?",
                ("order-1",),
            ).fetchone()
        assert row["executed_date"] == "2026-08-25"

    def test_unexplained_cash_change_finds_executed_orders(self, tmp_path):
        # The end-to-end reason executed_date exists: a same-day fill
        # should net out of the cash diff instead of reading as unexplained.
        db = tmp_path / "test.db"
        storage.init(db)
        with storage.connect(db) as conn:
            storage.save_portfolio(conn, "2026-08-21", "test-acc", "KRW",
                                   Decimal("500000"), Decimal("500000"), [])
            storage.save_order(
                conn, "2026-08-24", "2026-08-24T10:00:00", "test-acc",
                "102110", "SELL", 10, Decimal("109620"), "order-1",
                filled=True,
                execution={"filledQuantity": "10",
                          "averageFilledPrice": "10000",
                          "commission": "0", "tax": "0"},
                executed_date="2026-08-24",
            )
            # Sold 10 @ 10000 = +100,000 cash, matching the new balance
            # exactly - nothing unexplained.
            storage.save_portfolio(conn, "2026-08-24", "test-acc", "KRW",
                                   Decimal("600000"), Decimal("600000"), [])
            unexplained = storage.unexplained_cash_change(
                conn, "test-acc", "2026-08-24")
        assert unexplained == Decimal("0")


class TestDividendEvents:
    def test_round_trips(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        with storage.connect(db) as conn:
            storage.save_dividend_events(
                conn, "379790",
                [{"record_date": "2026-07-31", "amount": Decimal("210")},
                 {"record_date": "2026-01-05", "amount": Decimal("477")}],
                "2026-09-01T00:00:00",
            )
            loaded = storage.load_dividend_events(conn, "379790")
        assert loaded == [
            {"record_date": "2026-01-05", "amount": Decimal("477")},
            {"record_date": "2026-07-31", "amount": Decimal("210")},
        ]

    def test_resaving_the_same_event_replaces_not_duplicates(self, tmp_path):
        # A later, overlapping fetch window re-reports the same
        # (symbol, record_date) - it must overwrite, not accumulate.
        db = tmp_path / "test.db"
        storage.init(db)
        with storage.connect(db) as conn:
            storage.save_dividend_events(
                conn, "379790",
                [{"record_date": "2026-07-31", "amount": Decimal("210")}],
                "2026-08-01T00:00:00",
            )
            storage.save_dividend_events(
                conn, "379790",
                [{"record_date": "2026-07-31", "amount": Decimal("999")}],
                "2026-09-01T00:00:00",
            )
            loaded = storage.load_dividend_events(conn, "379790")
        assert loaded == [{"record_date": "2026-07-31",
                          "amount": Decimal("999")}]

    def test_load_all_groups_by_symbol(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        with storage.connect(db) as conn:
            storage.save_dividend_events(
                conn, "379790",
                [{"record_date": "2026-07-31", "amount": Decimal("210")}],
                "2026-09-01T00:00:00",
            )
            storage.save_dividend_events(
                conn, "133690",
                [{"record_date": "2026-08-04", "amount": Decimal("255")}],
                "2026-09-01T00:00:00",
            )
            loaded = storage.load_all_dividend_events(
                conn, ["379790", "133690", "102110"])
        assert loaded == {
            "379790": [{"record_date": "2026-07-31",
                       "amount": Decimal("210")}],
            "133690": [{"record_date": "2026-08-04",
                       "amount": Decimal("255")}],
            "102110": [],
        }


class TestDividendFetchState:
    def test_round_trips(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        with storage.connect(db) as conn:
            storage.save_dividend_fetch_state(
                conn, "379790", "2026-09-01", "2026-09-01T12:00:00")
            fetched_to = storage.dividend_fetch_state(conn, "379790")
        assert fetched_to == "2026-09-01"

    def test_missing_symbol_returns_none(self, tmp_path):
        db = tmp_path / "test.db"
        storage.init(db)
        with storage.connect(db) as conn:
            fetched_to = storage.dividend_fetch_state(conn, "999999")
        assert fetched_to is None
