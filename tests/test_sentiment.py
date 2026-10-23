import pytest

from quant import sentiment


def days(n):
    from datetime import date, timedelta
    d0 = date(2020, 1, 1)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def make(n, spx, vix, tlt, hyg, ief):
    ds = days(n)
    return {"^GSPC": (ds, spx), "^VIX": (ds, vix), "TLT": (ds, tlt), "HYG": (ds, hyg), "IEF": (ds, ief)}


class TestLabel:
    def test_bands(self):
        assert [sentiment.label(s) for s in (0, 24.9, 25, 50, 60, 75, 100)] == [
            "극단적 공포", "극단적 공포", "공포", "중립", "탐욕", "극단적 탐욕", "극단적 탐욕"]


class TestFearGreed:
    def test_missing_input_is_empty(self):
        assert sentiment.fear_greed({"^GSPC": (["2020-01-01"], [1.0])}) == {}

    def test_rally_with_falling_vix_reads_greed(self):
        n = 800
        spx = [100 * 1.001 ** i for i in range(n)]          # steady rally, accelerating at the end
        spx[-30:] = [spx[-31] * 1.004 ** k for k in range(1, 31)]
        vix = [20.0] * (n - 30) + [20 - k * 0.3 for k in range(30)]
        flat = [100.0] * n
        hyg = [100 * 1.0001 ** i for i in range(n)]
        hyg[-30:] = [hyg[-31] * 1.002 ** k for k in range(1, 31)]
        out = sentiment.fear_greed(make(n, spx, vix, flat, hyg, flat))
        assert out["score"] > 75 and out["label"] == "극단적 탐욕"
        assert len(out["history"]) == sentiment.HISTORY
        assert out["week_ago"] is not None and out["month_ago"] is not None

    def test_crash_with_spiking_vix_reads_fear(self):
        n = 800
        spx = [100 * 1.0005 ** i for i in range(n)]
        spx[-20:] = [spx[-21] * 0.98 ** k for k in range(1, 21)]
        vix = [15.0] * (n - 20) + [15 + k * 1.5 for k in range(20)]
        tlt = [100.0] * (n - 20) + [100 * 1.003 ** k for k in range(1, 21)]
        hyg = [100.0] * (n - 20) + [100 * 0.995 ** k for k in range(1, 21)]
        out = sentiment.fear_greed(make(n, spx, vix, tlt, hyg, [100.0] * n))
        assert out["score"] < 10 and out["label"] == "극단적 공포"
        assert all(c["score"] == pytest.approx(0, abs=1) for c in out["components"])

    def test_short_history_is_empty(self):
        n = 200
        out = sentiment.fear_greed(make(n, *([[100.0] * n] * 5)))
        assert out == {}


def kr(n, ks, fx, bond=None):
    ds = days(n)
    out = {"^KS11": (ds, ks), "KRW=X": (ds, fx)}
    if bond is not None:
        out["114260.KS"] = (ds, bond)
    return out


class TestKorea:
    def test_crash_with_won_selloff_reads_fear(self):
        n = 800
        ks = [2000 * 1.0003 ** i * (1 + 0.003 * (-1) ** i) for i in range(n)]
        ks[-20:] = [ks[-21] * 0.97 ** k * (1 + 0.02 * (-1) ** k) for k in range(1, 21)]
        fx = [1200.0] * (n - 20) + [1200 * 1.006 ** k for k in range(1, 21)]
        out = sentiment.kr_fear_greed(kr(n, ks, fx, [100.0] * n))
        assert out["score"] < 10 and len(out["components"]) == 4

    def test_three_components_without_bond_series(self):
        n = 800
        ks = [2000 * 1.0003 ** i * (1 + 0.003 * (-1) ** i) for i in range(n)]
        out = sentiment.kr_fear_greed(kr(n, ks, [1200.0 + i * 0.01 for i in range(n)]))
        assert out and out["components"][3]["score"] is None

    def test_history_spans_every_computable_day(self):
        n = 800
        ks = [2000 * 1.0003 ** i * (1 + 0.003 * (-1) ** i) for i in range(n)]
        h = sentiment.kr_history(kr(n, ks, [1200.0 + i * 0.01 for i in range(n)]))
        assert len(h) > sentiment.HISTORY and all(0 <= s <= 100 for _, s in h)

    def test_missing_input_is_empty(self):
        assert sentiment.kr_fear_greed({}) == {} and sentiment.kr_history({}) == []
