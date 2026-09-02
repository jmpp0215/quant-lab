from .config import PeadConfig

def fetch_investor_flow(symbol: str, start_date: str, end_date: str) -> dict:
    """
    [3차 레이어 스켈레톤] pykrx 투자자별 거래실적 API를 호출하여 수급 데이터를 가져옵니다.
    TODO: pykrx 연동 구현
    """
    return {}

def calculate_flow_score(
    symbol: str,
    rcept_dt: str,
    config: PeadConfig,
    flow_data: dict | None = None,
) -> dict:
    """
    [3차 레이어 스켈레톤] 공시 이후 N거래일간 기관/외국인/개인 순매수 비율을 계산합니다.

    Args:
        flow_data: fetch_investor_flow()로 별도 수집한 수급 데이터(주입용). signal.py의
                   calculate_surprise/build_event_timeline과 동일하게, 이 함수는 pykrx 등
                   외부 API를 직접 호출하지 않고 이미 수집된 데이터만 받아 계산합니다.

    Returns: { 'institutional': float, 'foreign': float, 'individual': float }
    TODO: 수급 점수 스코어링 로직 구현
    """
    return {'institutional': 0.0, 'foreign': 0.0, 'individual': 0.0}
