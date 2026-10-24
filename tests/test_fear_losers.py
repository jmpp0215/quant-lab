import pandas as pd
import pytest

from quant import fear_losers as fl


def snap(rows: dict[str, tuple]) -> pd.DataFrame:
    """code -> (close, volume, value, shares); market cap = close * shares."""
    df = pd.DataFrame.from_dict(rows, orient="index", columns=["종가", "거래량", "거래대금", "상장주식수"])
    df["시가총액"] = df["종가"] * df["상장주식수"]
    return df


class TestSectorMap:
    def test_groups_and_unknown(self):
        assert fl.group_of("반도체 제조업") == "전기전자"
        assert fl.group_of("선박 및 보트 건조업") == "운수장비"
        assert fl.group_of("소프트웨어 개발") == fl.OTHER
        assert fl.group_of(None) == fl.UNKNOWN
        assert fl.group_of(float("nan")) == fl.UNKNOWN

    def test_preferred_takes_common_industry(self):
        m = fl.sector_map(["000150", "000155", "000157"], ["두산", "두산우", "두산2우B"],
                          ["기타 금융업", None, None])
        assert m == {"000150": "지주·투자", "000155": "지주·투자", "000157": "지주·투자"}


class TestReturns:
    def test_plain_split_and_delist(self):
        a = snap({"a": (100, 1, 1, 10), "b": (100, 1, 1, 10), "c": (100, 1, 1, 10)})
        b = snap({"a": (110, 1, 1, 10), "b": (22, 1, 1, 50)})       # b split 1:5, cap 1100
        r = fl.returns(a, b, -1.0)
        assert r["a"] == pytest.approx(0.10)
        assert r["b"] == pytest.approx(0.10)
        assert r["c"] == -1.0
        assert pd.isna(fl.returns(a, b, None)["c"])

    def test_portfolio_return_fills_missing_with_delist(self):
        a = snap({"a": (100, 1, 1, 10), "c": (100, 1, 1, 10)})
        b = snap({"a": (120, 1, 1, 10)})
        assert fl.portfolio_return(a, b, ["a", "c"], 0.0) == pytest.approx(0.10)
        assert fl.portfolio_return(a, b, ["a", "c"], -1.0) == pytest.approx(-0.40)


class TestPick:
    def setup_method(self):
        # Sector X (6 names) fell ~30%; sector Y (6 names) flat except y0 fell 15%.
        # "pad" is the least traded name, so a 0% cut (strict >, as section 41) drops only it.
        prev, cur, self.sectors = {"pad": (100, 1, 1, 10)}, {"pad": (100, 1, 1, 10)}, {"pad": "Z"}
        for i in range(6):
            prev[f"x{i}"] = (100, 1, 100, 10)
            cur[f"x{i}"] = (70 - i, 1, 100 + i, 10)        # -30% .. -35%
            self.sectors[f"x{i}"] = "X"
            prev[f"y{i}"] = (100, 1, 100, 10)
            cur[f"y{i}"] = (85 if i == 0 else 100, 1, 200 + i, 10)
            self.sectors[f"y{i}"] = "Y"
        self.prev, self.cur = snap(prev), snap(cur)

    def test_raw_vs_sector_relative(self, monkeypatch):
        monkeypatch.setattr(fl, "N", 2)
        monkeypatch.setattr(fl, "LIQ_CUT", 0.0)
        p = fl.pick(self.prev, self.cur, self.sectors)
        assert p["raw"] == ["x5", "x4"]
        # y0: -15% vs rest of Y 0% -> -15pp; x5: -35% vs rest of X -31% -> -4pp
        assert p["sector_rel"][0] == "y0"
        assert p["sector_rel"][1] == "x5"

    def test_universe_filters(self, monkeypatch):
        monkeypatch.setattr(fl, "LIQ_CUT", 0.0)
        prev = pd.concat([self.prev, snap({"new": (0, 0, 0, 10), "halt": (100, 1, 1, 10)})])
        cur = pd.concat([self.cur, snap({"new": (50, 1, 100, 10), "halt": (100, 0, 0, 10)})])
        p = fl.pick(prev, cur, self.sectors)
        assert "new" not in p["universe"] and "halt" not in p["universe"]
        assert "pad" not in p["universe"]
        assert len(p["universe"]) == 12

    def test_liquidity_cut(self):
        p = fl.pick(self.prev, self.cur, self.sectors)    # 13 names, bottom 30% = pad, x0..x2
        assert set(p["universe"]) == {"x3", "x4", "x5"} | {f"y{i}" for i in range(6)}

    def test_small_or_unknown_sector_left_out_of_sector_rel(self, monkeypatch):
        monkeypatch.setattr(fl, "LIQ_CUT", 0.0)
        sectors = dict(self.sectors, y0=fl.UNKNOWN)          # Y now has 5 names: 4 peers each
        p = fl.pick(self.prev, self.cur, sectors)
        assert "y0" in p["raw"]
        assert not any(s.startswith("y") for s in p["sector_rel"])
        assert len(p["sector_rel"]) == 6


class TestSchedule:
    """fear_losers_log.due / episodes: which days get a record."""

    def setup_method(self):
        self.days = [f"2026-11-{d:02d}" for d in range(1, 31)]

    def due(self, tmp_path, monkeypatch, fear, today="2026-12-01", start="2026-11-01"):
        import fear_losers_log as fll
        monkeypatch.setattr(fll, "DIR", tmp_path)
        monkeypatch.setattr(fll, "LOG", tmp_path / "log")
        monkeypatch.setattr(fll, "START", start)
        from datetime import date
        return dict(fll.due(self.days, fear, date.fromisoformat(today)))

    def test_baseline_every_10th_day_from_start(self, tmp_path, monkeypatch):
        d = self.due(tmp_path, monkeypatch, {})
        assert [k for k, v in d.items() if v] == ["2026-11-01", "2026-11-11", "2026-11-21"]

    def test_fear_at_most_once_per_10_days(self, tmp_path, monkeypatch):
        fear = {x: 20.0 for x in self.days[3:16]} | {"2026-11-03": 25.0}
        d = self.due(tmp_path, monkeypatch, fear)
        assert [k for k, v in d.items() if "fear" in v] == ["2026-11-03", "2026-11-13"]

    def test_not_before_start_nor_today(self, tmp_path, monkeypatch):
        d = self.due(tmp_path, monkeypatch, {}, today="2026-11-21", start="2026-11-05")
        assert min(d) == "2026-11-05" and max(d) == "2026-11-20"

    def test_resumes_after_checked(self, tmp_path, monkeypatch):
        (tmp_path / "state.json").write_text('{"checked": "2026-11-15"}')
        d = self.due(tmp_path, monkeypatch, {})
        assert min(d) == "2026-11-16"
        assert [k for k, v in d.items() if v] == ["2026-11-21"]

    def test_episodes_group_within_20_days(self):
        import fear_losers_log as fll
        assert fll.episodes([0, 10, 30, 51, 60]) == [[0, 1, 2], [3, 4]]
