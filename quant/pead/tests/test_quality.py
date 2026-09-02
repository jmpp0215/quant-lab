from quant.pead.quality import calculate_quality_score
from quant.pead.config import PeadConfig


def test_calculate_quality_score_single_quarter_normal_case():
    # quality_lookback_quarters=1은 예전 단일 분기 로직과 동일하게 동작해야 한다.
    config = PeadConfig(dart_basis="CFS", quality_threshold=0.5, quality_lookback_quarters=1)
    current_rcept_dt = "20230515"

    normalized_records = [
        {"target_year": "2023", "report_code": "11013", "rcept_dt": "20230515", "quarterly_value": 100, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2023", "report_code": "11013", "rcept_dt": "20230515", "quarterly_value": 80, "metric": "operating_cash_flow", "basis": "CFS"},
    ]

    ratio, passes = calculate_quality_score("A", current_rcept_dt, config, normalized_records)
    assert ratio == 0.8
    assert passes is True  # 0.8 >= 0.5


def test_calculate_quality_score_below_threshold_does_not_pass():
    config = PeadConfig(dart_basis="CFS", quality_threshold=0.5, quality_lookback_quarters=1)
    current_rcept_dt = "20230515"

    normalized_records = [
        {"target_year": "2023", "report_code": "11013", "rcept_dt": "20230515", "quarterly_value": 100, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2023", "report_code": "11013", "rcept_dt": "20230515", "quarterly_value": 30, "metric": "operating_cash_flow", "basis": "CFS"},
    ]

    ratio, passes = calculate_quality_score("A", current_rcept_dt, config, normalized_records)
    assert ratio == 0.3
    assert passes is False


def test_calculate_quality_score_negative_oi_returns_none():
    # 평균 OI가 음수면 비율의 부호가 뒤집혀 의미가 없으므로 None을 반환해야 한다.
    config = PeadConfig(dart_basis="CFS", quality_threshold=0.5, quality_lookback_quarters=1)
    current_rcept_dt = "20230515"

    normalized_records = [
        {"target_year": "2023", "report_code": "11013", "rcept_dt": "20230515", "quarterly_value": -100, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2023", "report_code": "11013", "rcept_dt": "20230515", "quarterly_value": 50, "metric": "operating_cash_flow", "basis": "CFS"},
    ]

    ratio, passes = calculate_quality_score("A", current_rcept_dt, config, normalized_records)
    assert ratio is None
    assert passes is False


def test_calculate_quality_score_zero_oi_returns_none():
    config = PeadConfig(dart_basis="CFS", quality_threshold=0.5, quality_lookback_quarters=1)
    current_rcept_dt = "20230515"

    normalized_records = [
        {"target_year": "2023", "report_code": "11013", "rcept_dt": "20230515", "quarterly_value": 0, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2023", "report_code": "11013", "rcept_dt": "20230515", "quarterly_value": 50, "metric": "operating_cash_flow", "basis": "CFS"},
    ]

    ratio, passes = calculate_quality_score("A", current_rcept_dt, config, normalized_records)
    assert ratio is None
    assert passes is False


def test_calculate_quality_score_missing_ocf_returns_none():
    # 같은 분기의 OCF 데이터 자체가 없으면 계산할 수 없다.
    config = PeadConfig(dart_basis="CFS", quality_threshold=0.5, quality_lookback_quarters=1)
    current_rcept_dt = "20230515"

    normalized_records = [
        {"target_year": "2023", "report_code": "11013", "rcept_dt": "20230515", "quarterly_value": 100, "metric": "operating_income", "basis": "CFS"},
    ]

    ratio, passes = calculate_quality_score("A", current_rcept_dt, config, normalized_records)
    assert ratio is None
    assert passes is False


def test_calculate_quality_score_matches_oi_and_ocf_same_quarter_only():
    # OI의 최신 분기(23년 1Q)와 OCF의 최신 분기(22년 4Q, 아직 23년 1Q OCF 미공시)가
    # 다르면, 서로 다른 분기 값을 잘못 짝짓지 말고 계산 불가로 처리해야 한다.
    config = PeadConfig(dart_basis="CFS", quality_threshold=0.5, quality_lookback_quarters=1)
    current_rcept_dt = "20230515"

    normalized_records = [
        {"target_year": "2023", "report_code": "11013", "rcept_dt": "20230515", "quarterly_value": 100, "metric": "operating_income", "basis": "CFS"},
        # OCF는 22년 4Q 값만 존재 (23년 1Q OCF는 아직 없음)
        {"target_year": "2022", "report_code": "11011", "rcept_dt": "20230215", "quarterly_value": 90, "metric": "operating_cash_flow", "basis": "CFS"},
    ]

    ratio, passes = calculate_quality_score("A", current_rcept_dt, config, normalized_records)
    assert ratio is None
    assert passes is False


# ---------------------------------------------------------------------------
# 롤링 평균(quality_lookback_quarters > 1) 관련 테스트
# ---------------------------------------------------------------------------

def _four_quarter_records():
    # 2022Q2 -> 2022Q3 -> 2022Q4 -> 2023Q1(당기), quarter_index 연속
    return [
        {"target_year": "2022", "report_code": "11012", "rcept_dt": "20220815", "quarterly_value": 100, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2022", "report_code": "11012", "rcept_dt": "20220815", "quarterly_value": 60, "metric": "operating_cash_flow", "basis": "CFS"},
        {"target_year": "2022", "report_code": "11014", "rcept_dt": "20221115", "quarterly_value": 80, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2022", "report_code": "11014", "rcept_dt": "20221115", "quarterly_value": 70, "metric": "operating_cash_flow", "basis": "CFS"},
        {"target_year": "2022", "report_code": "11011", "rcept_dt": "20230215", "quarterly_value": 120, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2022", "report_code": "11011", "rcept_dt": "20230215", "quarterly_value": 100, "metric": "operating_cash_flow", "basis": "CFS"},
        {"target_year": "2023", "report_code": "11013", "rcept_dt": "20230515", "quarterly_value": 100, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2023", "report_code": "11013", "rcept_dt": "20230515", "quarterly_value": 90, "metric": "operating_cash_flow", "basis": "CFS"},
    ]


def test_calculate_quality_score_rolling_average_4q():
    # OI: [100(당기), 120, 80, 100] -> mean=100 / OCF: [90, 100, 70, 60] -> mean=80
    config = PeadConfig(dart_basis="CFS", quality_threshold=0.5, quality_lookback_quarters=4)
    ratio, passes = calculate_quality_score("A", "20230515", config, _four_quarter_records())
    assert ratio == 0.8
    assert passes is True


def test_calculate_quality_score_rolling_average_insufficient_quarters():
    # quality_lookback_quarters=4인데 2분기치 데이터뿐이면(예: 신규상장) 계산 불가.
    config = PeadConfig(dart_basis="CFS", quality_threshold=0.5, quality_lookback_quarters=4)
    records = [
        {"target_year": "2022", "report_code": "11011", "rcept_dt": "20230215", "quarterly_value": 120, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2022", "report_code": "11011", "rcept_dt": "20230215", "quarterly_value": 100, "metric": "operating_cash_flow", "basis": "CFS"},
        {"target_year": "2023", "report_code": "11013", "rcept_dt": "20230515", "quarterly_value": 100, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2023", "report_code": "11013", "rcept_dt": "20230515", "quarterly_value": 90, "metric": "operating_cash_flow", "basis": "CFS"},
    ]

    ratio, passes = calculate_quality_score("A", "20230515", config, records)
    assert ratio is None
    assert passes is False


def test_calculate_quality_score_rolling_average_quarter_gap_truncates_window():
    # 4분기를 요구하는데, 중간 분기(2022Q3)가 통째로 빠져 있으면 signal._align_periods가
    # 그 지점에서 끊어버려 연속된 분기가 2개(2023Q1, 2022Q4)뿐이 되므로 계산 불가여야 한다.
    config = PeadConfig(dart_basis="CFS", quality_threshold=0.5, quality_lookback_quarters=4)
    records = [
        {"target_year": "2022", "report_code": "11011", "rcept_dt": "20230215", "quarterly_value": 120, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2022", "report_code": "11011", "rcept_dt": "20230215", "quarterly_value": 100, "metric": "operating_cash_flow", "basis": "CFS"},
        # 2022Q3(11014) 누락
        {"target_year": "2022", "report_code": "11012", "rcept_dt": "20220815", "quarterly_value": 100, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2022", "report_code": "11012", "rcept_dt": "20220815", "quarterly_value": 60, "metric": "operating_cash_flow", "basis": "CFS"},
        {"target_year": "2023", "report_code": "11013", "rcept_dt": "20230515", "quarterly_value": 100, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2023", "report_code": "11013", "rcept_dt": "20230515", "quarterly_value": 90, "metric": "operating_cash_flow", "basis": "CFS"},
    ]

    ratio, passes = calculate_quality_score("A", "20230515", config, records)
    assert ratio is None
    assert passes is False


def test_calculate_quality_score_rolling_average_point_in_time_filtering():
    # 4분기 윈도우 안의 한 분기(2022Q4 OI)에 대한 미래 정정공시(rcept_dt 이후 발표)는
    # 배제되고, rcept_dt 시점에 가용했던 원본 값만 롤링 평균에 사용되어야 한다.
    config = PeadConfig(dart_basis="CFS", quality_threshold=0.5, quality_lookback_quarters=4)
    records = _four_quarter_records() + [
        # 2022Q4 OI 정정공시: rcept_dt(20230515) 이후 발표된 미래 정보이므로 무시되어야 함
        {"target_year": "2022", "report_code": "11011", "rcept_dt": "20230601", "quarterly_value": 999, "metric": "operating_income", "basis": "CFS"},
    ]

    ratio, passes = calculate_quality_score("A", "20230515", config, records)
    # 정정본(999)이 아니라 원본(120)이 사용되어야 하므로 mean_oi=100, ratio=0.8 그대로
    assert ratio == 0.8
    assert passes is True
