from .config import PeadConfig


def calculate_quality_score(
    symbol: str,
    rcept_dt: str,
    config: PeadConfig,
    normalized_records: list[dict] | None = None,
) -> tuple[float | None, bool]:
    """
    [2차 레이어] 최근 config.quality_lookback_quarters분기 평균 영업활동현금흐름 대비 평균
    영업이익 비율(trailing OCF/OI)을 계산하여 저품질 서프라이즈를 필터링합니다.

    단일 분기 비율 대신 롤링 평균을 쓰는 이유: 단일 분기 OCF/OI는 분기별로 매우 들쭉날쭉해서
    "이 회사가 원래 고품질인가"가 아니라 "그 분기에 우연히 현금흐름 전환이 좋았는가"만
    반영하는 경향이 있었습니다 (같은 종목이 분기에 따라 quality 상/하위를 오가는 현상이
    실제로 관측됨). 여러 분기를 평균하면 이 분기별 노이즈가 줄어듭니다.

    signal.calculate_surprise와 동일하게 point-in-time 원칙을 적용합니다: rcept_dt 시점에
    가용했던 최신 정정본만 사용하며(signal._get_point_in_time_series), 미래의 정정공시는
    배제합니다. "최근 N분기"는 operating_income의 point-in-time 시계열에서 SUE 계산과
    동일한 분기 연속성 규칙(signal._align_periods)으로 자릅니다 — 중간에 분기가 비면 그
    지점에서 끊어, 실제로는 연속되지 않은 분기들이 "최근 N분기"로 잘못 묶이는 것을
    방지합니다. OI 윈도우의 각 분기와 정확히 같은 (target_year, report_code)의
    operating_cash_flow 값을 찾아 짝짓습니다 — 서로 다른 분기의 OI/OCF가 섞이지 않도록
    합니다.

    OI 롤링 평균이 0 또는 음수이면 비율이 발산하거나 부호가 뒤집혀 의미가 없습니다 (예:
    평균 OI=-100, 평균 OCF=+50이면 비율은 -0.5이지만 실제로는 "적자 영업이익보다 현금흐름이
    낫다"는 좋은 신호가 나쁜 신호로 보이게 됨). 이 경우, 분기 수가 부족한 경우(신규상장 등),
    윈도우 내 일부 분기의 OCF가 없는 경우 모두 ocf_to_oi_ratio=None을 반환하며, 조용히
    0이나 다른 값으로 대체하지 않습니다.

    Args:
        normalized_records: DB(pead_quarterly_normalized)에서 조회한 해당 종목의 환산된
                             공시 이력(operating_income, operating_cash_flow 등, 주입용).
                             signal.calculate_surprise와 동일하게 이 함수는 DB/API를
                             직접 호출하지 않고 이미 수집된 데이터만 받아 계산합니다.

    Returns:
        (ocf_to_oi_ratio, passes_quality_filter).
        - 비율을 계산할 수 없으면 (None, False)를 반환합니다 — 품질을 검증할 수 없는
          경우를 관대하게 통과시키지 않습니다(fail-closed).
        - 계산 가능하면 passes_quality_filter = (ratio >= config.quality_threshold).
    """
    # 지연 import: signal.py가 (아직 배선되지 않은 combine_signal을 위해) quality.py를
    # import하고 있어, 모듈 최상단에서 서로를 import하면 순환 import가 됩니다.
    from .signal import _get_point_in_time_series, _align_periods

    if normalized_records is None:
        normalized_records = []

    oi_periods = _get_point_in_time_series(
        normalized_records, rcept_dt, "operating_income", config.dart_basis
    )
    oi_window = _align_periods(oi_periods)[: config.quality_lookback_quarters]
    if len(oi_window) < config.quality_lookback_quarters:
        return None, False

    mean_oi = sum(p["quarterly_value"] for p in oi_window) / len(oi_window)
    if mean_oi <= 0:
        return None, False

    ocf_periods = _get_point_in_time_series(
        normalized_records, rcept_dt, "operating_cash_flow", config.dart_basis
    )
    ocf_by_quarter = {
        (p["target_year"], p["report_code"]): p["quarterly_value"] for p in ocf_periods
    }
    ocf_values = []
    for p in oi_window:
        key = (p["target_year"], p["report_code"])
        if key not in ocf_by_quarter:
            return None, False
        ocf_values.append(ocf_by_quarter[key])
    mean_ocf = sum(ocf_values) / len(ocf_values)

    ratio = mean_ocf / mean_oi
    passes_filter = ratio >= config.quality_threshold
    return ratio, passes_filter
