from typing import List
from .config import PeadConfig

def fetch_universe_snapshot(date: str, config: PeadConfig) -> List[str]:
    """
    지정된 일자(date) 기준으로 config.universe 설정에 맞는 종목 리스트 스냅샷을 반환합니다.
    pykrx의 get_index_portfolio_deposit_file API를 활용하여 생존 편향(look-ahead bias)을 방지합니다.
    
    Args:
        date: YYYYMMDD 형식의 문자열 (예: 공시 접수일 t=0)
        config: PeadConfig 인스턴스 (universe 설정 포함)
        
    Returns:
        종목 코드(symbol) 리스트
    """
    # TODO: pykrx 연동하여 실제 스냅샷 조회
    # from pykrx import stock
    
    universe_type = config.universe
    
    if universe_type == 'kospi200':
        # 코스피 200 지수 구성종목 스냅샷 (지수 티커 '1028'은 예시)
        # return stock.get_index_portfolio_deposit_file("1028", date)
        return []
    elif universe_type == 'kospi_all':
        # 코스피 전체 종목 (시장 티커 'KOSPI')
        # return stock.get_market_ticker_list(date, market="KOSPI")
        return []
    elif universe_type == 'kospi_kosdaq_mid':
        # 코스피 + 코스닥 중소형주 유니버스
        # kospi_tickers = stock.get_market_ticker_list(date, market="KOSPI")
        # kosdaq_tickers = stock.get_market_ticker_list(date, market="KOSDAQ")
        # TODO: 중소형주 필터링 로직 (시가총액 기준 하위 N% 등)
        return []
    else:
        raise ValueError(f"Unknown universe type: {universe_type}")
