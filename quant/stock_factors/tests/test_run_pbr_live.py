from decimal import Decimal

import pandas as pd
import pytest

from quant.stock_factors.portfolio_construction import PBRConfig
from quant.stock_factors.scripts import pbr_storage
from quant.stock_factors.scripts.run_pbr_live import (
    filter_admin_stocks,
    resolve_target_weights,
)


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


@pytest.fixture
def pbr_db(tmp_path, monkeypatch):
    db_path = tmp_path / "pbr_live.db"
    monkeypatch.setattr(pbr_storage, "DB_PATH", db_path)
    pbr_storage.init()
    return db_path


def test_resolve_target_weights_retry_replays_last_incomplete_target(pbr_db):
    with pbr_storage.connect() as conn:
        pbr_storage.save_rebalance(
            conn, "2026-09-07", 50,
            Decimal("100"), Decimal("90"), Decimal("10"), Decimal("20"),
            {"005930": 0.6, "000660": 0.4},
            {"failed": ["005930"], "partial": [], "all_clear": False},
        )

    with pbr_storage.connect() as conn:
        weights = resolve_target_weights("2026-09-08", PBRConfig(), retry=True, conn=conn)

    assert weights == {"005930": Decimal("0.6"), "000660": Decimal("0.4")}


def test_resolve_target_weights_retry_raises_when_no_rebalance_recorded(pbr_db):
    with pbr_storage.connect() as conn:
        with pytest.raises(ValueError):
            resolve_target_weights("2026-09-08", PBRConfig(), retry=True, conn=conn)


def test_resolve_target_weights_retry_raises_when_last_rebalance_was_clean(pbr_db):
    with pbr_storage.connect() as conn:
        pbr_storage.save_rebalance(
            conn, "2026-09-07", 50,
            Decimal("100"), Decimal("100"), Decimal("10"), Decimal("10"),
            {"005930": 1.0},
            {"failed": [], "partial": [], "all_clear": True},
        )

    with pbr_storage.connect() as conn:
        with pytest.raises(ValueError):
            resolve_target_weights("2026-09-08", PBRConfig(), retry=True, conn=conn)


def test_resolve_target_weights_non_retry_builds_fresh_portfolio(pbr_db, monkeypatch):
    """retry=False must not touch pbr_storage at all - it recomputes the PBR
    ranking via construct_target_portfolio, same as before this change."""
    import quant.stock_factors.scripts.run_pbr_live as run_pbr_live

    called_with = {}

    def fake_construct(today_str, config):
        called_with["today_str"] = today_str
        called_with["config"] = config
        return {"005930": Decimal("1")}

    monkeypatch.setattr(run_pbr_live, "construct_target_portfolio", fake_construct)

    with pbr_storage.connect() as conn:
        weights = resolve_target_weights("2026-09-08", PBRConfig(), retry=False, conn=conn)

    assert weights == {"005930": Decimal("1")}
    assert called_with["today_str"] == "2026-09-08"
