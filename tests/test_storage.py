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
