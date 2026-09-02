from .config import PeadConfig

def calculate_quality_score(
    symbol: str,
    rcept_dt: str,
    config: PeadConfig,
    normalized_records: list[dict] | None = None,
) -> tuple[float, bool]:
    """
    [2차 레이어 스켈레톤] 영업활동현금흐름 대비 영업이익 비율(OCF/OI)을 계산하여
    저품질 서프라이즈를 필터링합니다.

    Args:
        normalized_records: DB(pead_quarterly_normalized)에서 조회한 해당 종목의 환산된 공시
                             이력(operating_cash_flow, operating_income 등, 주입용).
                             signal.py의 calculate_surprise와 동일하게, 이 함수는 DB/API를
                             직접 호출하지 않고 이미 수집된 데이터만 받아 계산합니다.

    Returns:
        (ocf_to_oi_ratio, passes_filter)

    TODO: 실제 OCF/OI 계산 로직 구현
    """
    return (0.0, True)
