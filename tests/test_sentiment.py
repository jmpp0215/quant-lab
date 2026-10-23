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
