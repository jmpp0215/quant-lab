from decimal import Decimal

import pandas as pd

from quant.stock_factors.scripts.run_pbr_live import filter_admin_stocks


def _fake_admin_listing():
    """Mirrors fdr.StockListing('KRX-ADMIN')'s actual shape: Symbol comes
    back as int64 (e.g. 40, not "000040"), unlike every other symbol column
    in this codebase (e.g. fdr.StockListing('KOSPI')'s Code, which is a
    zero-padded string). This is what caused the original bug - comparing
    zero-padded string symbols against this raw int64 column always fails
    to match."""
    return pd.DataFrame(
        {
            "Symbol": pd.array([36420, 93240], dtype="int64"),
            "Name": ["콘텐트리중앙", "형지엘리트"],
            "DesignationDate": ["2026-08-18", "2026-09-03"],
            "Reason": ["회생절차개시신청", "주가 미달(동전주)"],
        }
    )


def test_filter_admin_stocks_removes_admin_symbols_and_renormalizes():
    target_weights = {
        "036420": Decimal("0.25"),  # ADMIN (콘텐트리중앙)
        "093240": Decimal("0.25"),  # ADMIN (형지엘리트)
        "005930": Decimal("0.25"),  # not ADMIN
        "000660": Decimal("0.25"),  # not ADMIN
    }

    result = filter_admin_stocks(target_weights, _fake_admin_listing())

    assert set(result.keys()) == {"005930", "000660"}
    # Remaining weights re-normalize to sum to 1 rather than staying at 0.25 each.
    assert sum(result.values()) == Decimal("1")
    assert result["005930"] == Decimal("0.5")
    assert result["000660"] == Decimal("0.5")


def test_filter_admin_stocks_leaves_target_unchanged_when_no_overlap():
    target_weights = {"005930": Decimal("0.5"), "000660": Decimal("0.5")}

    result = filter_admin_stocks(target_weights, _fake_admin_listing())

    assert result == target_weights


def test_filter_admin_stocks_handles_empty_admin_listing():
    target_weights = {"005930": Decimal("1")}

    result = filter_admin_stocks(target_weights, pd.DataFrame(columns=["Symbol", "Name"]))

    assert result == target_weights
