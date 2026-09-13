import sqlite3

from quant.stock_factors import gpa
from quant.stock_factors.config import FactorConfig


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


def test_prepare_gpa_signals_uses_cfs_not_mean_of_cfs_and_ofs(tmp_path, monkeypatch):
    """Regression test: total_assets is stored as separate CFS/OFS rows for the
    same (symbol, rcept_dt, report_code), same as total_equity in value.py.
    Before this fix, prepare_gpa_signals selected both without filtering
    basis, so pandas.pivot_table's default aggfunc='mean' silently averaged
    them. GPA's denominator must come from CFS alone.
    """
    db_path = tmp_path / "test.db"
    _create_minimal_schema(db_path)
    monkeypatch.setattr(gpa, "DB_PATH", db_path)

    symbol = "000001"
    gross_profit = 50_000_000
    cfs_assets = 400_000_000
    ofs_assets = 395_000_000

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO pead_price_raw (date, symbol, close) VALUES ('20260901', ?, 1000)",
        (symbol,),
    )
    conn.execute(
        """INSERT INTO pead_quarterly_normalized
           (symbol, target_year, rcept_dt, report_code, metric, basis, quarterly_value)
           VALUES (?, '2026', '20260814', '11012', 'gross_profit', 'CFS', ?)""",
        (symbol, gross_profit),
    )
    for basis, value_amt in (("CFS", cfs_assets), ("OFS", ofs_assets)):
        conn.execute(
            """INSERT INTO pead_quarterly_normalized
               (symbol, target_year, rcept_dt, report_code, metric, basis, quarterly_value)
               VALUES (?, '2026', '20260814', '11012', 'total_assets', ?, ?)""",
            (symbol, basis, value_amt),
        )
    conn.commit()
    conn.close()

    config = FactorConfig(lookback_quarters=1, min_periods=1)
    df_gpa = gpa.prepare_gpa_signals("20260101", config)

    expected_gpa = gross_profit / cfs_assets
    mean_gpa = gross_profit / ((cfs_assets + ofs_assets) / 2)

    actual_gpa = df_gpa.loc["2026-09-01", symbol]
    assert actual_gpa == expected_gpa
    assert actual_gpa != mean_gpa
