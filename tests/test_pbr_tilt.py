import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant.pbr_tilt import tilt

ROOT = Path(__file__).resolve().parents[1]


def snap(rows):
    """rows: code -> (close, shares, volume, value, bps)."""
    df = pd.DataFrame.from_dict(rows, orient="index",
                                columns=["종가", "상장주식수", "거래량", "거래대금", "BPS"])
    df["시가총액"] = df["종가"] * df["상장주식수"]
    return df


class TestHolderReturn:
    def test_no_share_change_uses_price(self):
        r = tilt.holder_return(pd.Series([1.1]), pd.Series([1.0]), pd.Series([1.1]))
        assert r.iloc[0] == pytest.approx(0.1)

    def test_split_uses_cap_ratio(self):
        # 1:5 split: price /5, shares x5, cap unchanged -> holder return 0
        r = tilt.holder_return(pd.Series([0.2]), pd.Series([5.0]), pd.Series([1.0]))
        assert r.iloc[0] == pytest.approx(0.0)

    def test_issuance_uses_price_ratio(self):
        # shares x2 but price barely moved: new shares are not the holder's return
        r = tilt.holder_return(pd.Series([0.95]), pd.Series([2.0]), pd.Series([1.9]))
        assert r.iloc[0] == pytest.approx(-0.05)

    def test_consolidation_uses_cap_ratio(self):
        r = tilt.holder_return(pd.Series([10.0]), pd.Series([0.1]), pd.Series([1.02]))
        assert r.iloc[0] == pytest.approx(0.02)


class TestBuildPanel:
    def test_ret_gap_suspect_and_snap_numbers(self):
        p = tilt.build_panel({
            "2026-01-02": snap({"1": (100, 10, 5, 500, 50), "000002": (10, 10, 5, 50, 5)}),
            "2026-01-16": snap({"1": (110, 10, 5, 550, 50)}),
            "2026-01-30": snap({"1": (250, 10, 5, 1250, 50), "000002": (12, 10, 5, 60, 5)}),
        })
        a = p[p.code == "000001"].set_index("snap")
        assert np.isnan(a.loc[0, "ret"]) and np.isnan(a.loc[0, "gap"])
        assert a.loc[1, "ret"] == pytest.approx(0.1) and a.loc[1, "gap"] == 1
        assert a.loc[2, "ret"] == pytest.approx(250 / 110 - 1) and bool(a.loc[2, "suspect"])
        b = p[p.code == "000002"].set_index("snap")
        assert b.loc[2, "gap"] == 2 and b.loc[2, "ret"] == pytest.approx(0.2)  # against last present
        assert not bool(b.loc[2, "suspect"])

    def test_duplicate_code_keeps_first_row(self):
        s = snap({"1": (100, 10, 5, 500, 50)})
        s = pd.concat([s, snap({"1": (100, 10, 5, 500, 60)})])
        p = tilt.build_panel({"2026-01-02": s})
        assert len(p) == 1 and p.loc[0, "bps"] == 50


class TestTilt:
    def test_weights_sum_to_one_and_follow_score(self):
        a0 = {"a": 0.5, "b": 0.3, "c": 0.2}
        q = pd.Series({"a": 1 / 3, "b": 2 / 3, "c": 1.0})
        w = tilt.tilt(a0, q)
        assert sum(w.values()) == pytest.approx(1.0)
        raw = {"a": 0.5 * (0.5 + 1 / 3), "b": 0.3 * (0.5 + 2 / 3), "c": 0.2 * 1.5}
        for s in a0:
            assert w[s] == pytest.approx(raw[s] / sum(raw.values()))

    def test_multiplier_range_is_half_to_one_and_half(self):
        names = [f"s{i}" for i in range(50)]
        q = tilt.percentile(pd.Series(range(50), index=names), names)
        a0 = {s: 1 / 50 for s in names}
        w = tilt.tilt(a0, q)
        assert max(w.values()) / min(w.values()) == pytest.approx(1.5 / (0.5 + 1 / 50))

    def test_percentile_missing_is_neutral(self):
        q = tilt.percentile(pd.Series({"a": 1.0, "c": 3.0}), ["a", "b", "c"])
        assert q["b"] == q.median() and q["c"] == 1.0

    def test_percentile_all_missing(self):
        q = tilt.percentile(pd.Series(dtype=float), ["a", "b"])
        assert (q == 0.5).all()


class TestHold:
    def closes(self):
        return pd.DataFrame({"a": [100.0, 110.0, 121.0], "b": [50.0, np.nan, 45.0],
                             "c": [10.0, 10.0, 10.0]}, index=["d0", "d1", "d2"])

    def test_buy_and_hold_gross_and_drift(self):
        h = tilt.hold({"a": 0.5, "b": 0.5}, self.closes())
        assert h["stock_ret"]["a"] == pytest.approx(0.21)
        assert h["stock_ret"]["b"] == pytest.approx(-0.1)  # halted day keeps last close
        assert h["gross"] == pytest.approx(0.5 * 0.21 + 0.5 * -0.1)
        ew = h["end_weights"]
        assert ew["a"] == pytest.approx(0.5 * 1.21 / (0.5 * 1.21 + 0.5 * 0.9))

    def test_artifact_move_counts_as_zero(self):
        c = pd.DataFrame({"a": [100.0, 1000.0, 1100.0]}, index=["d0", "d1", "d2"])
        h = tilt.hold({"a": 1.0}, c)
        assert h["artifacts"] == 1 and h["gross"] == pytest.approx(0.1)

    def test_symbol_without_prices_stays_flat(self):
        h = tilt.hold({"a": 0.5, "z": 0.5}, self.closes())
        assert h["missing"] == ["z"] and h["gross"] == pytest.approx(0.5 * 0.21)


class TestTradeCost:
    def test_from_cash_is_all_buys(self):
        cost, turnover = tilt.trade_cost(None, {"a": 0.6, "b": 0.4})
        assert turnover == pytest.approx(1.0) and cost == pytest.approx(tilt.BUY)

    def test_partial_rebalance(self):
        cost, turnover = tilt.trade_cost({"a": 0.7, "b": 0.3}, {"a": 0.5, "c": 0.5})
        # sell a 0.2 and b 0.3, buy c 0.5
        assert turnover == pytest.approx(1.0)
        assert cost == pytest.approx(0.5 * tilt.SELL + 0.5 * tilt.BUY)


class TestRankIC:
    def test_perfect_and_constant(self):
        s = pd.Series({"a": 1.0, "b": 2.0, "c": 3.0})
        assert tilt.rank_ic(s, s * 10) == pytest.approx(1.0)
        assert tilt.rank_ic(s, pd.Series({"a": 0.0, "b": 0.0, "c": 0.0})) == 0.0


def test_frozen_signal_is_the_competition_entry():
    blob = (ROOT / "quant/pbr_tilt/carbon_f1abddde67.py").read_bytes()
    assert hashlib.sha256(blob).hexdigest()[:10] == "f1abddde67"
