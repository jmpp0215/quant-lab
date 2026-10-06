"""refresh_prices.py: KIS-sourced incremental refresh. No live KIS calls -
a fake client serves daily_chart from a fixed set of bars."""
import sqlite3
from datetime import date

from quant.pead.scripts import refresh_prices as rp


class FakeClient:
    """daily_chart over {YYYYMMDD: close}, newest first, capped at 100 rows
    like the real endpoint."""

    def __init__(self, closes):
        self.closes = closes
        self.calls = []

    def daily_chart(self, symbol, start, end, *, adjusted=False):
        self.calls.append((symbol, start, end, adjusted))
        days = sorted((d for d in self.closes if start <= d <= end), reverse=True)[:100]
        return {"output2": [
            {"stck_bsop_date": d, "stck_oprc": "1", "stck_hgpr": "2", "stck_lwpr": "1",
             "stck_clpr": str(self.closes[d]), "acml_vol": "10"} for d in days]}


def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE pead_price_raw (date TEXT, symbol TEXT, open REAL, high REAL,
            low REAL, close REAL, volume REAL, trading_value REAL,
            PRIMARY KEY (date, symbol));
        CREATE TABLE pead_price_fetch_state (symbol TEXT PRIMARY KEY,
            fetched_to TEXT NOT NULL, updated_at TEXT NOT NULL);
    """)
    return conn


def weekdays(start, end):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d = date.fromordinal(d.toordinal() + 1)
    return out


class TestSyncSymbol:
    def test_todays_bar_is_not_stored(self):
        # 12:30 run: the endpoint already has today's forming bar.
        conn = db()
        conn.execute("INSERT INTO pead_price_fetch_state VALUES ('000020', '20261001', 'x')")
        client = FakeClient({"20261001": 100, "20261002": 101, "20261006": 999})
        rp.sync_symbol(conn, client, "000020", date(2026, 10, 6))
        stored = dict(conn.execute("SELECT date, close FROM pead_price_raw"))
        assert stored == {"20261001": 100.0, "20261002": 101.0}
        assert all(not adj for *_, adj in client.calls)  # 원주가

    def test_overlap_overwrites_an_earlier_partial_bar(self):
        conn = db()
        conn.execute("INSERT INTO pead_price_raw VALUES ('20261001', '000020', 0, 0, 0, 55, 3, 0)")
        conn.execute("INSERT INTO pead_price_fetch_state VALUES ('000020', '20261001', 'x')")
        rp.sync_symbol(conn, FakeClient({"20261001": 57}), "000020", date(2026, 10, 6))
        assert conn.execute("SELECT close, volume FROM pead_price_raw").fetchone() == (57.0, 10.0)

    def test_already_synced_today_is_skipped(self):
        conn = db()
        conn.execute("INSERT INTO pead_price_fetch_state VALUES ('000020', '20261006', 'x')")
        client = FakeClient({"20261002": 1})
        rp.sync_symbol(conn, client, "000020", date(2026, 10, 6))
        assert client.calls == []

    def test_first_sync_pages_past_the_100_row_cap(self):
        conn = db()
        days = weekdays(date(2020, 1, 1), date(2021, 6, 30))  # ~390 weekdays
        rp.sync_symbol(conn, FakeClient({d: 1 for d in days}), "000020", date(2021, 7, 1))
        n = conn.execute("SELECT COUNT(*) FROM pead_price_raw").fetchone()[0]
        assert n == len(days)

    def test_no_rows_leaves_state_for_a_retry(self):
        conn = db()
        conn.execute("INSERT INTO pead_price_fetch_state VALUES ('000020', '20261001', 'x')")
        rp.sync_symbol(conn, FakeClient({}), "000020", date(2026, 10, 6))
        assert conn.execute("SELECT fetched_to FROM pead_price_fetch_state").fetchone()[0] == "20261001"


class TestFetchDaily:
    def test_window_never_hits_the_row_cap(self):
        # The widest window must stay under 100 weekdays, or a page would be
        # silently truncated by the endpoint's cap.
        start = date(2026, 1, 5)
        end = date.fromordinal(start.toordinal() + rp.WINDOW_DAYS)
        assert len(weekdays(start, end)) < 100

