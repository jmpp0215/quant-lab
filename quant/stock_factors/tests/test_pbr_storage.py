from decimal import Decimal

from quant.stock_factors.scripts import pbr_storage


def _make_db(tmp_path, monkeypatch):
    db_path = tmp_path / "pbr_live.db"
    monkeypatch.setattr(pbr_storage, "DB_PATH", db_path)
    pbr_storage.init()
    return db_path


def test_get_latest_rebalance_none_when_empty(tmp_path, monkeypatch):
    _make_db(tmp_path, monkeypatch)
    with pbr_storage.connect() as conn:
        assert pbr_storage.get_latest_rebalance(conn) is None
        assert pbr_storage.get_latest_full_rebalance_date(conn) is None


def test_get_latest_rebalance_parses_summary_and_target(tmp_path, monkeypatch):
    _make_db(tmp_path, monkeypatch)
    with pbr_storage.connect() as conn:
        pbr_storage.save_rebalance(
            conn, "2026-09-07", 50,
            Decimal("100"), Decimal("100"), Decimal("10"), Decimal("10"),
            {"005930": 0.5, "000660": 0.5},
            {"failed": ["005930"], "partial": [], "all_clear": False},
        )

    with pbr_storage.connect() as conn:
        latest = pbr_storage.get_latest_rebalance(conn)

    assert latest["trade_date"] == "2026-09-07"
    assert latest["all_clear"] is False
    assert latest["failed"] == ["005930"]
    assert latest["partial"] == []
    assert latest["retry_of"] is None
    assert latest["target_portfolio"] == {"005930": 0.5, "000660": 0.5}


def test_get_latest_full_rebalance_date_skips_retry_rows(tmp_path, monkeypatch):
    _make_db(tmp_path, monkeypatch)
    with pbr_storage.connect() as conn:
        pbr_storage.save_rebalance(
            conn, "2026-09-07", 50,
            Decimal("100"), Decimal("100"), Decimal("10"), Decimal("10"),
            {"005930": 1.0},
            {"failed": ["005930"], "partial": [], "all_clear": False},
        )
        pbr_storage.save_rebalance(
            conn, "2026-09-08", 50,
            Decimal("100"), Decimal("100"), Decimal("10"), Decimal("10"),
            {"005930": 1.0},
            {"failed": [], "partial": [], "all_clear": True, "retry_of": "2026-09-07"},
        )

    with pbr_storage.connect() as conn:
        # Latest row overall is the retry (09-08); the full rebalance is still 09-07.
        assert pbr_storage.get_latest_rebalance(conn)["trade_date"] == "2026-09-08"
        assert pbr_storage.get_latest_full_rebalance_date(conn) == "2026-09-07"


def test_save_rebalance_same_day_rerun_does_not_crash_and_overwrites(tmp_path, monkeypatch):
    """Regression test for the PK-collision bug: re-running save_rebalance for
    the same trade_date used to raise sqlite3.IntegrityError (INSERT with no
    upsert against trade_date's PRIMARY KEY), rolling back the whole
    transaction - including pbr_orders rows for real orders already placed
    that attempt. It must instead overwrite the summary row."""
    _make_db(tmp_path, monkeypatch)
    with pbr_storage.connect() as conn:
        pbr_storage.save_rebalance(
            conn, "2026-09-07", 50,
            Decimal("100"), Decimal("90"), Decimal("10"), Decimal("20"),
            {"005930": 1.0},
            {"failed": ["005930"], "partial": [], "all_clear": False},
        )
        pbr_storage.save_order(conn, "2026-09-07", "005930", "BUY", 10,
                               Decimal("70000"), 0, None, "order-1")

    # Same-day retry: re-running save_rebalance must not raise, and the new
    # order from this attempt must coexist with the first attempt's order.
    with pbr_storage.connect() as conn:
        pbr_storage.save_rebalance(
            conn, "2026-09-07", 50,
            Decimal("100"), Decimal("100"), Decimal("10"), Decimal("10"),
            {"005930": 1.0},
            {"failed": [], "partial": [], "all_clear": True},
        )
        pbr_storage.save_order(conn, "2026-09-07", "005930", "BUY", 10,
                               Decimal("70000"), 10, Decimal("70000"), "order-2")

    with pbr_storage.connect() as conn:
        rows = conn.execute("SELECT * FROM pbr_rebalance WHERE trade_date='2026-09-07'").fetchall()
        assert len(rows) == 1
        latest = pbr_storage.get_latest_rebalance(conn)
        assert latest["all_clear"] is True

        orders = conn.execute("SELECT broker_order_id FROM pbr_orders WHERE trade_date='2026-09-07'").fetchall()
        assert {o["broker_order_id"] for o in orders} == {"order-1", "order-2"}


def _flag_row(symbol, rcept_no, title_class="neutral", llm_severe=None):
    return {"symbol": symbol, "rcept_no": rcept_no, "rcept_dt": "20261001",
            "report_nm": "x", "title_class": title_class, "llm_severe": llm_severe,
            "llm_category": None, "llm_reason": None, "llm_model": None}


STATS = {"n_symbols": 2, "n_llm_calls": 1, "input_tokens": 100,
         "output_tokens": 10, "cost_usd": Decimal("0.001"), "errors": 0}


def test_disclosure_run_round_trip(tmp_path, monkeypatch):
    _make_db(tmp_path, monkeypatch)
    with pbr_storage.connect() as conn:
        assert not pbr_storage.disclosure_run_exists(conn, "2026-10-16")
        pbr_storage.save_disclosure_run(conn, "2026-10-16", [
            _flag_row("000001", "a"), _flag_row("000002", "b", "review", False)], STATS)

    with pbr_storage.connect() as conn:
        assert pbr_storage.disclosure_run_exists(conn, "2026-10-16")
        rows = conn.execute("SELECT symbol, llm_severe FROM disclosure_flags "
                            "ORDER BY symbol").fetchall()
        run = conn.execute("SELECT n_filings, cost_usd FROM disclosure_runs").fetchone()
    # NULL stays NULL ("not judged"), False is stored as 0.
    assert [tuple(r) for r in rows] == [("000001", None), ("000002", 0)]
    assert tuple(run) == (2, "0.001")


def test_disclosure_rerun_replaces_the_earlier_rows(tmp_path, monkeypatch):
    _make_db(tmp_path, monkeypatch)
    with pbr_storage.connect() as conn:
        pbr_storage.save_disclosure_run(conn, "2026-10-16", [_flag_row("000001", "a")], STATS)
        pbr_storage.save_disclosure_run(conn, "2026-10-16", [_flag_row("000002", "b")], STATS)
        symbols = [r[0] for r in conn.execute("SELECT symbol FROM disclosure_flags")]
    assert symbols == ["000002"]


def test_get_rebalance_target(tmp_path, monkeypatch):
    _make_db(tmp_path, monkeypatch)
    with pbr_storage.connect() as conn:
        pbr_storage.save_rebalance(conn, "2026-09-14", 50, Decimal("1"), Decimal("1"),
                                   Decimal("1"), Decimal("1"), {"005930": 1.0}, {})
        assert pbr_storage.get_rebalance_target(conn, "2026-09-14") == {"005930": 1.0}
        assert pbr_storage.get_rebalance_target(conn, "2026-09-15") is None
