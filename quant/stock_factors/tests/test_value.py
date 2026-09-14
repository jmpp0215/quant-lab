import sqlite3

import pandas as pd

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


def test_compute_quarantine_windows_confirms_on_next_filing():
    # 007460 case: 293 -> 4530 on 20260508 (15:1 reverse split artifact),
    # confirming issued_shares filing lands 20260814.
    price_series_by_sym = {
        "007460": {"20260504": 293.0, "20260507": 293.0, "20260508": 4530.0, "20260511": 4000.0},
    }
    issued_shares_rcept_by_sym = {"007460": ["20260323", "20260814"]}

    windows = value.compute_quarantine_windows(price_series_by_sym, issued_shares_rcept_by_sym)

    assert windows["007460"] == [("20260508", "20260814")]


def test_compute_quarantine_windows_stays_open_without_confirmation():
    price_series_by_sym = {
        "AAA": {"20260901": 1000.0, "20260902": 5000.0, "20260903": 5050.0},
    }
    # No issued_shares filing at all for this symbol yet
    windows = value.compute_quarantine_windows(price_series_by_sym, {})

    assert windows["AAA"] == [("20260902", None)]


def test_compute_quarantine_windows_merges_repeated_anomalies_before_confirmation():
    # A second anomaly fires before the first one's confirming filing arrives -
    # the two should collapse into a single window, not two overlapping entries.
    price_series_by_sym = {
        "BBB": {
            "20260101": 100.0,
            "20260102": 1000.0,   # anomaly 1
            "20260201": 1010.0,
            "20260202": 10100.0,  # anomaly 2, still inside anomaly 1's open window
        },
    }
    issued_shares_rcept_by_sym = {"BBB": ["20260215"]}
    # The single 20260215 filing lands after both event dates, so it confirms
    # both at once - the merged result must be one window, not two.

    windows = value.compute_quarantine_windows(price_series_by_sym, issued_shares_rcept_by_sym)

    assert windows["BBB"] == [("20260102", "20260215")]


def test_compute_quarantine_windows_merges_repeated_open_anomalies():
    # Both anomalies remain unconfirmed (no filing at all) - should still
    # collapse into a single open window rather than two duplicate entries.
    price_series_by_sym = {
        "BBB": {
            "20260101": 100.0,
            "20260102": 1000.0,   # anomaly 1, unconfirmed
            "20260201": 1010.0,
            "20260202": 10100.0,  # anomaly 2, unconfirmed
        },
    }
    windows = value.compute_quarantine_windows(price_series_by_sym, {})

    assert windows["BBB"] == [("20260102", None)]


def test_compute_quarantine_windows_no_anomaly_no_window():
    price_series_by_sym = {"CCC": {"20260101": 100.0, "20260102": 101.0, "20260103": 99.0}}
    windows = value.compute_quarantine_windows(price_series_by_sym, {})
    assert "CCC" not in windows


def test_get_pbr_signal_func_excludes_quarantined_symbol():
    dates = pd.to_datetime(["20260508", "20260601"])
    # QUAR has the lowest PBR (most attractive) but is quarantined through 20260814
    df_bps = pd.DataFrame({"QUAR": [100.0, 100.0], "CLEAN": [100.0, 100.0]}, index=dates)
    df_price = pd.DataFrame({"QUAR": [10.0, 10.0], "CLEAN": [50.0, 50.0]}, index=dates)
    # pad past the len(bps) < 50 guard
    for i in range(50):
        df_bps[f"pad{i}"] = 100.0
        df_price[f"pad{i}"] = 90.0  # PBR=0.9, worse than CLEAN's 0.5 but better than padding needs

    quarantine = {"QUAR": [("20260508", "20260814")]}
    signal_func = value.get_pbr_signal_func(df_bps, df_price, top_percentile=0.1, quarantine=quarantine)

    selected = signal_func("20260601")
    assert "QUAR" not in selected
    assert "CLEAN" in selected
