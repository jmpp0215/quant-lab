from typing import Dict
from .config import PeadConfig

# 기존 ETF 전략의 가중치 로직 재사용
from quant.allocation import risk_parity, to_decimal_weights

def apply_weighting_scheme(symbols: list[str], price_series: Dict[str, list[float]], config: PeadConfig) -> Dict[str, float]:
    """
    [최종 가중치 스켈레톤] config.weighting_scheme 에 따라 ('equal' 또는 'inverse_vol') 
    최종 포트폴리오 비중을 산출합니다.
    
    참고: 
    - risk_parity 및 to_decimal_weights는 ETF 개수/종목에 무관하게 범용적으로 작성되어 있으므로
      PEAD에서도 별도 수정 없이 주입받은 price_series로 cov 행렬만 구성하여 바로 주입 가능합니다.
      (data/signal 계층 분리 원칙에 따라 가격 데이터는 외부에서 주입받습니다)
      
    TODO: cov 행렬 구성 및 risk_parity 호출을 통한 실 가중치 배분 로직 구현
    """
    return {sym: 1.0 / len(symbols) for sym in symbols} if symbols else {}
