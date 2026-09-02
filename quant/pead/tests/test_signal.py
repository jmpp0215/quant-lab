import pytest
from quant.pead.signal import _compute_sue, calculate_surprise
from quant.pead.config import PeadConfig

def test_compute_sue():
    past_values = [
        120, # 당기
        110, # -1
        100, # -2
        90,  # -3
        100, # -4 (전년 동기)
        90,  # -5
        100, # -6
        80,  # -7
        90,  # -8
    ]
    current_value = 120
    sue = _compute_sue(current_value, past_values, lookback=4)
    assert sue == pytest.approx(2.449, 0.01)

def test_compute_sue_insufficient_data():
    past_values = [120, 110, 100, 90]
    sue = _compute_sue(120, past_values, lookback=4)
    assert sue == 0.0
    
def test_compute_sue_insufficient_history():
    past_values = [120, 110, 100, 90, 100]
    sue = _compute_sue(120, past_values, lookback=4)
    assert sue == 0.0
    
def test_compute_sue_zero_variance():
    past_values = [
        120, 110, 110, 110, 120, 110, 110, 110, 120
    ]
    sue = _compute_sue(120, past_values, lookback=4)
    assert sue == 0.0

from quant.pead.dart import normalize_to_quarterly

def test_normalization_chain():
    # 4분기가 순서대로 주어지는 정상 케이스 검증
    raw = [
        {"symbol": "A", "target_year": "2023", "report_code": "11013", "metric": "OI", "basis": "CFS", "rcept_dt": "20230515", "value": 100}, # 1Q
        {"symbol": "A", "target_year": "2023", "report_code": "11012", "metric": "OI", "basis": "CFS", "rcept_dt": "20230814", "value": 250}, # Half (Q1+Q2) -> Q2 = 150
        {"symbol": "A", "target_year": "2023", "report_code": "11014", "metric": "OI", "basis": "CFS", "rcept_dt": "20231114", "value": 450}, # 3Q (Q1+Q2+Q3) -> Q3 = 200
        {"symbol": "A", "target_year": "2023", "report_code": "11011", "metric": "OI", "basis": "CFS", "rcept_dt": "20240315", "value": 700}, # Annual -> Q4 = 250
    ]
    norm = normalize_to_quarterly(raw)
    assert len(norm) == 4
    
    q1 = next(r for r in norm if r['report_code'] == '11013')
    assert q1['quarterly_value'] == 100
    assert q1['is_estimable'] == True
    
    q2 = next(r for r in norm if r['report_code'] == '11012')
    assert q2['quarterly_value'] == 150
    assert q2['is_estimable'] == True
    
    q3 = next(r for r in norm if r['report_code'] == '11014')
    assert q3['quarterly_value'] == 200
    
    q4 = next(r for r in norm if r['report_code'] == '11011')
    assert q4['quarterly_value'] == 250

def test_normalization_missing_previous_quarter():
    # 1Q 누락된 상태에서 반기 보고서 접수
    raw = [
        {"symbol": "A", "target_year": "2023", "report_code": "11012", "metric": "OI", "basis": "CFS", "rcept_dt": "20230814", "value": 250}, 
    ]
    norm = normalize_to_quarterly(raw)
    assert len(norm) == 1
    assert norm[0]['quarterly_value'] is None
    assert norm[0]['is_estimable'] == False

def test_normalization_point_in_time():
    # 미래의 정정공시가 포함된 경우 필터링 검증
    # 반기(11012) 계산 시점(20230814)에 가용한 1분기(11013) 데이터를 사용해야 함
    raw = [
        # 1Q 원본
        {"symbol": "A", "target_year": "2023", "report_code": "11013", "metric": "OI", "basis": "CFS", "rcept_dt": "20230515", "value": 100}, 
        
        # 반기 원본
        {"symbol": "A", "target_year": "2023", "report_code": "11012", "metric": "OI", "basis": "CFS", "rcept_dt": "20230814", "value": 250}, 
        
        # 1Q 정정 (반기 보고서 이후인 9월에 발생한 미래 정보)
        {"symbol": "A", "target_year": "2023", "report_code": "11013", "metric": "OI", "basis": "CFS", "rcept_dt": "20230915", "value": 120}, 
    ]
    norm = normalize_to_quarterly(raw)
    
    # 반기 계산(20230814 기준)에는 1Q 원본(100)이 사용되어야 하므로 quarterly_value = 150
    q2 = next(r for r in norm if r['report_code'] == '11012')
    assert q2['quarterly_value'] == 150

def test_calculate_surprise_point_in_time():
    config = PeadConfig(lookback_quarters=4, earnings_metric="operating_income", dart_basis="CFS")
    current_rcept_dt = "20230515" # 23년 1Q(11013) 기준일

    normalized_records = [
        # 22년 1Q
        {"target_year": "2022", "report_code": "11013", "rcept_dt": "20220515", "quarterly_value": 90, "metric": "operating_income", "basis": "CFS"},
        # 22년 2Q
        {"target_year": "2022", "report_code": "11012", "rcept_dt": "20220815", "quarterly_value": 80, "metric": "operating_income", "basis": "CFS"},
        # 22년 3Q
        {"target_year": "2022", "report_code": "11014", "rcept_dt": "20221115", "quarterly_value": 100, "metric": "operating_income", "basis": "CFS"},
        # 22년 4Q
        {"target_year": "2022", "report_code": "11011", "rcept_dt": "20230215", "quarterly_value": 90, "metric": "operating_income", "basis": "CFS"},

        # 23년 1Q (당기)
        {"target_year": "2023", "report_code": "11013", "rcept_dt": "20230515", "quarterly_value": 100, "metric": "operating_income", "basis": "CFS"},

        # 22년 1Q 정정공시 (22년 6월 발표, 23년 5월 시점에는 이미 가용)
        {"target_year": "2022", "report_code": "11013", "rcept_dt": "20220615", "quarterly_value": 100, "metric": "operating_income", "basis": "CFS"},

        # 22년 2Q 정정공시 (23년 6월 발표, t=0 기준 미래 정보이므로 무시되어야 함)
        {"target_year": "2022", "report_code": "11012", "rcept_dt": "20230601", "quarterly_value": 999, "metric": "operating_income", "basis": "CFS"},
    ]

    # 올바른 필터링 후 시계열: 100 (당기), 90 (4Q), 100 (3Q), 80 (2Q, 원본사용), 100 (1Q, 정정본사용)
    # YoY diff = 100 - 100 = 0
    sue, is_estimable = calculate_surprise("A", current_rcept_dt, config, normalized_records)
    assert is_estimable is True
    assert sue == 0.0


def test_calculate_surprise_filters_by_metric_and_basis():
    # operating_income(CFS)만 config에서 지정했는데, DB에는 net_income/OFS 등 다른
    # metric·basis 레코드도 같은 (year, report_code)로 섞여 저장되어 있는 상황을 재현.
    # 필터링이 없으면 임의의 metric 값이 시계열에 섞여 들어갈 수 있다.
    config = PeadConfig(lookback_quarters=4, earnings_metric="operating_income", dart_basis="CFS")
    current_rcept_dt = "20230515"

    oi_series = [
        ("2022", "11013", "20220515", 90),
        ("2022", "11012", "20220815", 80),
        ("2022", "11014", "20221115", 100),
        ("2022", "11011", "20230215", 90),
        ("2023", "11013", "20230515", 130),  # 당기, operating_income은 130
    ]
    normalized_records = [
        {"target_year": y, "report_code": rc, "rcept_dt": dt, "quarterly_value": v, "metric": "operating_income", "basis": "CFS"}
        for (y, rc, dt, v) in oi_series
    ]
    # 같은 (year, report_code, rcept_dt) 조합에 다른 metric/basis 값을 섞어 넣는다.
    # 필터링이 되지 않으면 이 값들이 period_map에서 operating_income 값을 덮어쓸 수 있다.
    for (y, rc, dt, _v) in oi_series:
        normalized_records.append(
            {"target_year": y, "report_code": rc, "rcept_dt": dt, "quarterly_value": 999999, "metric": "net_income", "basis": "CFS"}
        )
        normalized_records.append(
            {"target_year": y, "report_code": rc, "rcept_dt": dt, "quarterly_value": -999999, "metric": "operating_income", "basis": "OFS"}
        )

    sue, is_estimable = calculate_surprise("A", current_rcept_dt, config, normalized_records)
    assert is_estimable is True
    # operating_income/CFS만으로 계산했을 때와 동일한 결과여야 한다 (다른 metric/basis에 오염되지 않음)
    expected_sue, expected_estimable = calculate_surprise(
        "A", current_rcept_dt, config,
        [r for r in normalized_records if r["metric"] == "operating_income" and r["basis"] == "CFS"],
    )
    assert is_estimable == expected_estimable
    assert sue == pytest.approx(expected_sue)


def test_calculate_surprise_quarter_gap_is_not_estimable():
    # 23년 1Q(11013, 정확히 "전년동기"에 해당하는 분기)가 통째로 누락된 상황.
    # 인덱스로만 정렬해서 past_values[4]를 그대로 "전년동기"로 취급하면, 실제로는 5분기 전인
    # 22년 4Q 값이 그 자리를 차지하게 되어 조용히 잘못된 SUE가 계산될 위험이 있다.
    # 이런 경우는 계산을 포기하고 (None, False)를 반환해야 한다.
    config = PeadConfig(lookback_quarters=4, earnings_metric="operating_income", dart_basis="CFS")
    current_rcept_dt = "20240515"  # 24년 1Q 기준

    normalized_records = [
        {"target_year": "2022", "report_code": "11013", "rcept_dt": "20220515", "quarterly_value": 50, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2022", "report_code": "11012", "rcept_dt": "20220815", "quarterly_value": 55, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2022", "report_code": "11014", "rcept_dt": "20221115", "quarterly_value": 70, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2022", "report_code": "11011", "rcept_dt": "20230215", "quarterly_value": 60, "metric": "operating_income", "basis": "CFS"},
        # 2023 11013(1Q, 진짜 전년동기) 누락
        {"target_year": "2023", "report_code": "11012", "rcept_dt": "20230815", "quarterly_value": 85, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2023", "report_code": "11014", "rcept_dt": "20231115", "quarterly_value": 95, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2023", "report_code": "11011", "rcept_dt": "20240215", "quarterly_value": 80, "metric": "operating_income", "basis": "CFS"},
        {"target_year": "2024", "report_code": "11013", "rcept_dt": "20240515", "quarterly_value": 100, "metric": "operating_income", "basis": "CFS"},
    ]

    sue, is_estimable = calculate_surprise("A", current_rcept_dt, config, normalized_records)
    assert is_estimable is False
    assert sue is None
