import sqlite3

from quant.stock_factors import value


def _create_minimal_schema(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE pead_price_raw (
            date TEXT NOT NULL, symbol TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL,
            volume REAL, trading_value REAL,
            PRIMARY KEY (date, symbol)
        )
    """)
    conn.execute("""
        CREATE TABLE pead_quarterly_normalized (
            symbol TEXT NOT NULL, target_year TEXT NOT NULL, rcept_dt TEXT NOT NULL,
            report_code TEXT NOT NULL, metric TEXT NOT NULL, basis TEXT NOT NULL,
            quarterly_value REAL, is_estimable INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (symbol, target_year, rcept_dt, report_code, metric, basis)
        )
    """)
    conn.commit()
    conn.close()


def test_prepare_pbr_signals_uses_cfs_not_mean_of_cfs_and_ofs(tmp_path, monkeypatch):
    """Regression test: pead_quarterly_normalized stores CFS (consolidated) and
    OFS (standalone) total_equity as separate rows for the same
    (symbol, rcept_dt, report_code). Before this fix, prepare_pbr_signals
    selected both without filtering basis, so pandas.pivot_table's default
    aggfunc='mean' silently averaged them into a meaningless book value.
    BPS must be computed from CFS alone.
    """
    db_path = tmp_path / "test.db"
    _create_minimal_schema(db_path)
    monkeypatch.setattr(value, "DB_PATH", db_path)

    symbol = "000001"
    cfs_equity = 227_144_196_140
    ofs_equity = 226_756_927_127
    issued_shares = 12_607_989

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO pead_price_raw (date, symbol, close) VALUES ('20260901', ?, 1000)",
        (symbol,),
    )
    for basis, value_amt in (("CFS", cfs_equity), ("OFS", ofs_equity)):
        conn.execute(
            """INSERT INTO pead_quarterly_normalized
               (symbol, target_year, rcept_dt, report_code, metric, basis, quarterly_value)
               VALUES (?, '2026', '20260814', '11012', 'total_equity', ?, ?)""",
            (symbol, basis, value_amt),
        )
    conn.execute(
        """INSERT INTO pead_quarterly_normalized
           (symbol, target_year, rcept_dt, report_code, metric, basis, quarterly_value)
           VALUES (?, '2026', '20260814', '11012', 'issued_shares', 'CFS', ?)""",
        (symbol, issued_shares),
    )
    conn.commit()
    conn.close()

    df_bps = value.prepare_pbr_signals("20260101")

    expected_bps = cfs_equity / issued_shares
    mean_bps = (cfs_equity + ofs_equity) / 2 / issued_shares

    actual_bps = df_bps.loc["2026-09-01", symbol]
    assert actual_bps == expected_bps
    assert actual_bps != mean_bps
