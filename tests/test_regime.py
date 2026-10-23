import pytest

from quant import regime


class TestTrailingPct:
    def test_needs_half_window(self):
        assert regime.trailing_pct([1.0] * 629) is None

    def test_rank_of_last(self):
        vals = list(range(1000)) + [500.5]
        assert regime.trailing_pct(vals) == pytest.approx(100 * 501 / 1000)

    def test_only_last_five_years(self):
        vals = [100.0] * 2000 + [1.0] * 1259 + [2.0]
        assert regime.trailing_pct(vals) == 100.0


class TestBreadth:
    def test_share_above_own_average(self):
        up = [float(i) for i in range(200)]
        down = [float(200 - i) for i in range(200)]
        assert regime.breadth({"a": up, "b": down, "c": up}) == pytest.approx(200 / 3)

    def test_short_history_left_out(self):
        assert regime.breadth({"a": [1.0] * 149}) is None
        assert regime.breadth({"a": [1.0] * 149, "b": [float(i) for i in range(160)]}) == 100.0


class TestFeatures:
    def test_drawdown_and_returns(self):
        closes = [100.0] * 200 + [200.0] + [150.0] * 60
        f = regime.features(closes)
        assert f["dd_1y"] == pytest.approx(-0.25)
        assert f["r20"] == pytest.approx(0.0)
        assert f["r60"] == pytest.approx(150 / 200 - 1)


class TestTriggered:
    def test_thresholds_inclusive(self):
        assert regime.triggered(70, 30)
        assert not regime.triggered(69.9, 30)
        assert not regime.triggered(85, 30.1)
        assert not regime.triggered(None, 10)
