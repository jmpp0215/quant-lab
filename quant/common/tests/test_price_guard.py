import pytest

from quant.common.price_guard import PriceAnomaly, find_anomalies, safe_return


def test_safe_return_normal_move():
    ret, flagged = safe_return(100.0, 105.0)
    assert ret == pytest.approx(0.05)
    assert flagged is False


def test_safe_return_flags_large_jump():
    # 15:1 reverse split artifact, matching the 에이프로젠 case
    ret, flagged = safe_return(293.0, 4530.0)
    assert ret is None
    assert flagged is True


def test_safe_return_flags_large_drop():
    ret, flagged = safe_return(1000.0, 400.0)
    assert ret is None
    assert flagged is True


def test_safe_return_missing_price_not_flagged():
    ret, flagged = safe_return(None, 100.0)
    assert ret is None
    assert flagged is False
    ret, flagged = safe_return(100.0, None)
    assert ret is None
    assert flagged is False


def test_safe_return_custom_threshold():
    # 20% move: flagged only under a tighter threshold
    ret, flagged = safe_return(100.0, 120.0, threshold=0.5)
    assert flagged is False
    ret, flagged = safe_return(100.0, 120.0, threshold=0.1)
    assert flagged is True


def test_find_anomalies_detects_single_event():
    price_series = {
        "20260504": 293.0,
        "20260506": 293.0,
        "20260507": 293.0,
        "20260508": 4530.0,
        "20260511": 4000.0,
    }
    anomalies = find_anomalies("007460", price_series)
    assert len(anomalies) == 1
    assert anomalies[0] == PriceAnomaly("007460", "20260507", "20260508", pytest.approx(14.4607, rel=1e-3))


def test_find_anomalies_respects_date_window():
    price_series = {
        "20260504": 293.0,
        "20260507": 293.0,
        "20260508": 4530.0,
        "20260511": 4000.0,
    }
    # window starting after the anomaly date should not see it
    assert find_anomalies("007460", price_series, start_date="20260509") == []
    # window ending before the anomaly date should not see it
    assert find_anomalies("007460", price_series, end_date="20260507") == []
    # window covering the anomaly date should see it
    assert len(find_anomalies("007460", price_series, start_date="20260508", end_date="20260508")) == 1


def test_find_anomalies_no_false_positive_on_normal_series():
    price_series = {"20260101": 100.0, "20260102": 103.0, "20260103": 98.0, "20260104": 101.0}
    assert find_anomalies("AAA", price_series) == []
