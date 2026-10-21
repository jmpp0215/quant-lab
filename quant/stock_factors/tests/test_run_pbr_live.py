from decimal import Decimal

import pandas as pd
import pytest

from quant.stock_factors.portfolio_construction import PBRConfig
from quant.stock_factors.scripts import pbr_storage
from quant.stock_factors.scripts.run_pbr_live import (
    apply_admin_filter,
    build_summary,
    equal_weight_shadow,
    filter_admin_stocks,
    inverse_vol_fell_back,
    over_rank_boundary,
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
        weights, boundary = resolve_target_weights("2026-09-08", PBRConfig(), retry=True, conn=conn)

    assert weights == {"005930": Decimal("0.6"), "000660": Decimal("0.4")}
    assert boundary == {}


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
    ranking via construct_target_portfolio_with_boundary, same as before this change."""
    import quant.stock_factors.scripts.run_pbr_live as run_pbr_live

    called_with = {}

    def fake_construct(today_str, config):
        called_with["today_str"] = today_str
        called_with["config"] = config
        return {"005930": Decimal("1")}, {"005930": Decimal("1100")}

    monkeypatch.setattr(run_pbr_live, "construct_target_portfolio_with_boundary", fake_construct)

    with pbr_storage.connect() as conn:
        weights, boundary = resolve_target_weights("2026-09-08", PBRConfig(), retry=False, conn=conn)

    assert weights == {"005930": Decimal("1")}
    assert boundary == {"005930": Decimal("1100")}
    assert called_with["today_str"] == "2026-09-08"


class TestApplyAdminFilter:
    def test_reports_which_symbols_were_removed(self):
        target = {"036420": Decimal("0.25"), "005930": Decimal("0.25"),
                  "093240": Decimal("0.25"), "000660": Decimal("0.25")}
        weights, removed, error = apply_admin_filter(target, _fake_admin_listing)
        assert removed == ["036420", "093240"]
        assert set(weights) == {"005930", "000660"}
        assert error is None

    def test_nothing_removed_is_an_empty_list(self):
        target = {"005930": Decimal("1")}
        weights, removed, error = apply_admin_filter(target, _fake_admin_listing)
        assert (weights, removed, error) == (target, [], None)

    def test_listing_failure_passes_weights_through_and_records_the_error(self):
        # 2026-10-02: fdr.StockListing returned an HTTP 404. The guardrail
        # then didn't run at all - that must reach the summary.
        def broken():
            raise RuntimeError("HTTP Error 404: Not Found")
        target = {"036420": Decimal("0.5"), "005930": Decimal("0.5")}
        weights, removed, error = apply_admin_filter(target, broken)
        assert weights == target
        assert removed == []
        assert "404" in error


class TestInverseVolFellBack:
    def test_identical_weights_mean_fallback(self):
        w = {s: Decimal("0.0222") for s in ("A", "B", "C")}
        assert inverse_vol_fell_back(w, "inverse_vol") is True

    def test_distinct_weights_mean_inverse_vol_applied(self):
        w = {"A": Decimal("0.5"), "B": Decimal("0.3"), "C": Decimal("0.2")}
        assert inverse_vol_fell_back(w, "inverse_vol") is False

    def test_not_applicable_for_equal_scheme(self):
        w = {s: Decimal("0.5") for s in ("A", "B")}
        assert inverse_vol_fell_back(w, "equal") is None

    def test_single_name_is_not_called_a_fallback(self):
        assert inverse_vol_fell_back({"A": Decimal("1")}, "inverse_vol") is False


class TestBuildSummary:
    def test_records_admin_weighting_and_fallback(self):
        s = build_summary(["000670"], [], {"009440"}, None, "inverse_vol", False,
                          ["002820", "004270"], None)
        assert s == {
            "failed": ["000670"], "partial": [], "all_clear": False, "halted": ["009440"],
            "weighting_scheme": "inverse_vol", "inverse_vol_fallback": False,
            "admin_removed": ["002820", "004270"], "admin_filter_error": None,
        }

    def test_retry_of_only_when_retrying(self):
        assert "retry_of" not in build_summary([], [], set(), None, "equal", None, [], None)
        s = build_summary([], [], set(), "2026-09-14", "replayed", None, [], None)
        assert s["retry_of"] == "2026-09-14"
        assert s["all_clear"] is True


class TestEqualWeightShadow:
    def test_same_names_equal_weights(self):
        iv = {"A": Decimal("0.5"), "B": Decimal("0.3"), "C": Decimal("0.2")}
        assert equal_weight_shadow(iv) == {"A": 0.333333, "B": 0.333333, "C": 0.333333}

    def test_empty_target(self):
        assert equal_weight_shadow({}) == {}

    def test_recorded_in_summary_only_when_given(self):
        base = build_summary([], [], set(), None, "inverse_vol", False, [], None)
        assert "equal_weight_target" not in base
        s = build_summary([], [], set(), None, "inverse_vol", False, [], None,
                          equal_weight_target={"A": 0.5, "B": 0.5})
        assert s["equal_weight_target"] == {"A": 0.5, "B": 0.5}


class TestOverRankBoundary:
    def test_only_names_priced_above_their_boundary(self):
        # 2026-09-14's 023530: ranked in on a 112,000 close, bought at 120,200
        target = {"A": Decimal("0.5"), "B": Decimal("0.5")}
        boundary = {"A": Decimal("113260"), "B": Decimal("58560")}
        prices = {"A": Decimal("120200"), "B": Decimal("56600")}
        assert over_rank_boundary(target, boundary, prices) == {
            "A": {"live": 120200.0, "boundary": 113260.0}}

    def test_at_the_boundary_is_not_over(self):
        target = {"A": Decimal("1")}
        assert over_rank_boundary(target, {"A": Decimal("1000")},
                                  {"A": Decimal("1000")}) == {}

    def test_missing_boundary_or_price_is_skipped(self):
        # retry runs have no boundary; a 0 price means no quote was found
        target = {"A": Decimal("0.5"), "B": Decimal("0.5")}
        assert over_rank_boundary(target, {}, {"A": Decimal("1")}) == {}
        assert over_rank_boundary(target, {"B": Decimal("10")},
                                  {"B": Decimal("0")}) == {}

    def test_admin_removed_names_are_not_reported(self):
        # boundary covers the pre-admin-filter top N; only the traded target counts
        assert over_rank_boundary({"A": Decimal("1")},
                                  {"A": Decimal("10"), "X": Decimal("10")},
                                  {"A": Decimal("9"), "X": Decimal("20")}) == {}

    def test_recorded_in_summary_only_with_a_boundary(self):
        base = build_summary([], [], set(), None, "inverse_vol", False, [], None)
        assert "rank_boundary" not in base and "over_boundary" not in base
        s = build_summary([], [], set(), None, "inverse_vol", False, [], None,
                          rank_boundary={"A": 1100.0}, over_boundary={})
        assert s["rank_boundary"] == {"A": 1100.0}
        assert s["over_boundary"] == {}


class TestRetryWindow:
    """A manual --retry is held to check_pbr_due's window too: the last full
    rebalance must be at most RETRY_WINDOW_TRADING_DAYS (3) weekdays old."""

    @staticmethod
    def _save(conn, trade_date, summary):
        pbr_storage.save_rebalance(
            conn, trade_date, 50,
            Decimal("100"), Decimal("90"), Decimal("10"), Decimal("20"),
            {"005930": 1.0}, summary)

    def test_within_window_replays(self, pbr_db):
        with pbr_storage.connect() as conn:   # Mon 09-07 -> Thu 09-10 = 3
            self._save(conn, "2026-09-07", {"failed": ["005930"], "partial": [], "all_clear": False})
            weights, _ = resolve_target_weights("2026-09-10", PBRConfig(), retry=True, conn=conn)
        assert weights == {"005930": Decimal("1.0")}

    def test_past_window_refuses(self, pbr_db):
        with pbr_storage.connect() as conn:   # Mon 09-07 -> Fri 09-11 = 4
            self._save(conn, "2026-09-07", {"failed": ["005930"], "partial": [], "all_clear": False})
            with pytest.raises(ValueError, match="retry window"):
                resolve_target_weights("2026-09-11", PBRConfig(), retry=True, conn=conn)

    def test_weekend_does_not_count(self, pbr_db):
        with pbr_storage.connect() as conn:   # Thu 09-10 -> Tue 09-15 = 3
            self._save(conn, "2026-09-10", {"failed": ["005930"], "partial": [], "all_clear": False})
            resolve_target_weights("2026-09-15", PBRConfig(), retry=True, conn=conn)

    def test_window_counts_from_the_full_rebalance_not_the_last_retry(self, pbr_db):
        with pbr_storage.connect() as conn:
            self._save(conn, "2026-09-07", {"failed": ["005930"], "partial": [], "all_clear": False})
            self._save(conn, "2026-09-09", {"failed": ["005930"], "partial": [], "all_clear": False,
                                            "retry_of": "2026-09-07"})
            with pytest.raises(ValueError, match="retry window"):
                resolve_target_weights("2026-09-11", PBRConfig(), retry=True, conn=conn)
