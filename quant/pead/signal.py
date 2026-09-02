from typing import Dict
from .config import PeadConfig
from .quality import calculate_quality_score
from .flow import calculate_flow_score

_REPORT_QUARTER_ORDER = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}


def _quarter_index(target_year: str, report_code: str) -> int | None:
    """연도+분기를 정수 하나로 환산 (year*4 + (quarter-1)). 두 분기가 1씩 차이나면 연속된 분기임을
    의미하며, 이를 이용해 중간 분기 누락을 감지합니다."""
    quarter = _REPORT_QUARTER_ORDER.get(report_code)
    if quarter is None:
        return None
    return int(target_year) * 4 + (quarter - 1)


def _get_point_in_time_series(
    normalized_records: list[dict], current_rcept_dt: str, metric: str, basis: str
) -> list[dict]:
    """
    과거 공시 내역(normalized_records)에서 current_rcept_dt 시점에 가용했던, config가 지정한
    metric/basis에 해당하는 단일 분기 환산값들을 최신순으로 정렬하여 반환합니다.
    미래의 정정공시는 배제(look-ahead bias 방지)하며, is_estimable=False 인 항목은 제외합니다.

    다른 metric(예: net_income)이나 다른 basis(예: OFS)가 섞여 들어오는 것을 막기 위해
    metric/basis로 명시적으로 필터링합니다 — 호출자가 이미 걸러서 넘겼을 것이라고 가정하지 않습니다.

    반환값은 quarter_index(연도*4+분기)를 포함하므로, 호출자가 분기 연속성(중간 분기 누락 여부)을
    검증할 수 있습니다.
    """
    valid_records = [
        r
        for r in normalized_records
        if r["rcept_dt"] <= current_rcept_dt
        and r.get("is_estimable", True)
        and r["metric"] == metric
        and r["basis"] == basis
    ]

    # 동일한 (target_year, report_code)에 대해 여러 번 공시가 있었을 경우 가장 최신 정정본 선택
    period_map: dict[tuple, dict] = {}
    for r in valid_records:
        period_key = (r["target_year"], r["report_code"], r["metric"], r["basis"])
        if period_key not in period_map or r["rcept_dt"] > period_map[period_key]["rcept_dt"]:
            period_map[period_key] = r

    periods = []
    for (target_year, report_code, _metric, _basis), r in period_map.items():
        quarter_index = _quarter_index(target_year, report_code)
        if quarter_index is None:
            continue
        periods.append(
            {
                "quarter_index": quarter_index,
                "target_year": target_year,
                "report_code": report_code,
                "quarterly_value": r["quarterly_value"],
            }
        )

    periods.sort(key=lambda p: p["quarter_index"], reverse=True)
    return periods


def _align_quarterly_series(periods: list[dict]) -> list[float]:
    """
    quarter_index가 최신부터 1씩 연속으로 감소하는 구간만 남기고, 중간에 분기가 비면 그 지점에서
    자릅니다. 이렇게 해야 정렬된 리스트의 i번째 값이 항상 정확히 "i분기 전" 값이라는
    _compute_sue의 가정이 깨지지 않습니다 (분기가 하나 비면 그 뒤 인덱스가 전부 밀려서 예:
    전년 동기 자리에 5분기 전 값이 들어가는 식의 조용한 오계산을 방지).
    """
    if not periods:
        return []
    aligned = []
    expected_index = periods[0]["quarter_index"]
    for p in periods:
        if p["quarter_index"] != expected_index:
            break
        aligned.append(p["quarterly_value"])
        expected_index -= 1
    return aligned


def calculate_surprise(
    symbol: str,
    rcept_dt: str,
    config: PeadConfig,
    normalized_records: list[dict] = None,
) -> tuple[float | None, bool]:
    """
    [1차 레이어] SUE(Standardized Unexpected Earnings)를 계산합니다.
    항상 실행되며, (당기 - 전년동기) / (최근 config.lookback_quarters 분기 YoY 변화율 표준편차) 산식을 사용합니다.

    config.earnings_metric / config.dart_basis에 해당하는 데이터만 골라서 계산하며, 중간 분기
    누락 등으로 분기 연속성이 깨져 신뢰할 수 있는 계산이 불가능한 경우 조용히 잘못된 값을
    반환하는 대신 (None, False)를 반환합니다.

    Args:
        symbol: 종목코드
        rcept_dt: 현재 이벤트(공시) 접수일 (t=0)
        config: PEAD 설정
        normalized_records: DB(pead_quarterly_normalized)에서 조회한 해당 종목의 환산된 공시 이력
                             (주입용). metric/basis 필터링은 이 함수 내부에서 수행하므로 호출자는
                             미리 걸러서 넘길 필요가 없습니다.

    Returns:
        (surprise_score, is_estimable) — is_estimable=False면 surprise_score는 None.
    """
    if normalized_records is None:
        normalized_records = []

    periods = _get_point_in_time_series(
        normalized_records, rcept_dt, config.earnings_metric, config.dart_basis
    )
    aligned = _align_quarterly_series(periods)

    if len(aligned) < 5:
        return None, False

    current_value = aligned[0]
    score = _compute_sue(current_value, aligned, config.lookback_quarters)
    return score, True


def _compute_sue(current_value: float, past_values: list[float], lookback: int) -> float:
    """
    과거 데이터(past_values)는 최신부터 과거 순으로 정렬된 리스트라고 가정.
    [당기, 1분기 전, 2분기 전, 3분기 전, 4분기 전(전년동기), ...]
    """
    if len(past_values) < 5:
        return 0.0 # 전년 동기 데이터 없음

    yoy_diff = current_value - past_values[4]

    # 과거 lookback 분기 동안의 YoY diff 리스트 생성
    yoy_history = []
    # i=1부터 lookback까지 (최대 1분기 전 ~ lookback 분기 전)
    for i in range(1, lookback + 1):
        if i + 4 < len(past_values):
            yoy_history.append(past_values[i] - past_values[i+4])

    if len(yoy_history) < 2:
        return 0.0 # 표준편차를 구하기에 데이터 부족

    mean_yoy = sum(yoy_history) / len(yoy_history)
    variance = sum((x - mean_yoy) ** 2 for x in yoy_history) / (len(yoy_history) - 1)
    std_dev = variance ** 0.5

    if std_dev == 0:
        return 0.0

    return yoy_diff / std_dev


def combine_signal(symbol: str, rcept_dt: str, config: PeadConfig) -> float:
    """
    각 레이어의 점수를 조합하여 최종 score를 산출합니다.
    quality.py와 flow.py의 산출 함수를 호출하여 각 레이어의 원점수를 DB에 기록(항상 수행)한 후,
    config.enable_quality_filter 및 config.enable_flow_overlay 플래그가
    True일 때만 해당 점수를 필터링 및 가점에 반영합니다.
    결과는 pead_combined_scores에 config_hash와 함께 저장됩니다.
    """
    # TODO: Implement layer combination logic
    # _ = calculate_quality_score(symbol, rcept_dt, config, normalized_records=...)
    # _ = calculate_flow_score(symbol, rcept_dt, config, flow_data=...)
    return 0.0

def build_event_timeline(
    symbol: str, rcept_dt: str, price_series: Dict[str, float], config: PeadConfig
) -> dict | None:
    """
    공시 접수일(t=0)을 기준으로 config.entry_timing(t+1/t+2)에 따른 진입 가격을 산출하고,
    진입 이후 5, 10, 20, 40, 60 거래일의 누적 수익률을 계산합니다.
    외부(data 계층)에서 주입받은 price_series만 사용합니다.

    Args:
        price_series: {날짜(YYYYMMDD 문자열): 종가} 딕셔너리. rcept_dt 이후 구간의 거래일이
                      포함되어 있어야 합니다 (정렬은 이 함수 내부에서 수행).
        config: config.entry_timing('t+1' 또는 't+2')에 따라 진입 시점이 결정됩니다.

    Returns:
        pead_event_returns에 저장 가능한 형태의 dict, 또는 진입에 필요한 거래일이 부족하면 None.
    """
    trading_dates = sorted(d for d in price_series if d >= rcept_dt)
    if not trading_dates:
        return None

    entry_offset = {"t+1": 1, "t+2": 2}[config.entry_timing]
    if len(trading_dates) <= entry_offset:
        return None

    entry_date = trading_dates[entry_offset]
    entry_price = price_series[entry_date]

    horizons = {"return_5d": 5, "return_10d": 10, "return_20d": 20, "return_40d": 40, "return_60d": 60}
    returns = {}
    for field, horizon in horizons.items():
        target_idx = entry_offset + horizon
        if target_idx < len(trading_dates):
            target_price = price_series[trading_dates[target_idx]]
            returns[field] = (target_price - entry_price) / entry_price
        else:
            returns[field] = None

    return {
        "symbol": symbol,
        "rcept_dt": rcept_dt,
        "entry_timing": config.entry_timing,
        "entry_price": entry_price,
        **returns,
    }
