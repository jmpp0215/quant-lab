import pytest

from quant.amzn import signal


class TestComputeRsi:
    def test_insufficient_samples_returns_none(self):
        assert signal.compute_rsi([100.0] * signal.MIN_RSI_SAMPLES) is not None
        assert signal.compute_rsi([100.0] * (signal.MIN_RSI_SAMPLES - 1)) is None

    def test_uptrend(self):
        closes = [100, 102, 101, 103, 105, 104, 106, 108, 107, 109, 111, 110, 112, 114, 113]
        assert signal.compute_rsi(closes) == pytest.approx(78.26086956521739)

    def test_strict_decline_avg_gain_zero(self):
        closes = [130 - 2 * i for i in range(15)]
        assert signal.compute_rsi(closes) == pytest.approx(0.0)

    def test_flat_series_avg_loss_zero_returns_100(self):
        closes = [100.0] * 20
        assert signal.compute_rsi(closes) == 100.0


class TestPercentileRank:
    def test_boundaries(self):
        history = list(range(20, 61, 2))  # 21 values: 20,22,...,58,60
        assert signal.percentile_rank(history, history[0]) == pytest.approx(100 / 21)
        assert signal.percentile_rank(history, history[-1]) == pytest.approx(100.0)

    def test_value_below_all_history(self):
        history = [10.0, 20.0, 30.0]
        assert signal.percentile_rank(history, 5.0) == 0.0

    def test_empty_history_raises(self):
        with pytest.raises(ValueError):
            signal.percentile_rank([], 10.0)


class TestClassifyRsi:
    def test_boundaries(self):
        assert signal.classify_rsi(30) == "과매도"
        assert signal.classify_rsi(30.1) == "중립"
        assert signal.classify_rsi(70) == "과매수"
        assert signal.classify_rsi(69.9) == "중립"


class TestClassifyValuationPercentile:
    def test_boundaries(self):
        assert signal.classify_valuation_percentile(30) == "저평가"
        assert signal.classify_valuation_percentile(30.1) == "중립"
        assert signal.classify_valuation_percentile(70) == "고평가"
        assert signal.classify_valuation_percentile(69.9) == "중립"
