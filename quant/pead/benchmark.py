import pandas as pd

from .config import PeadConfig
from .signal import build_event_timeline


def calculate_excess_return(
    event_symbol: str,
    rcept_dt: str,
    stock_return_20d: float,
    benchmark_price_series: pd.Series,
    config: PeadConfig,
) -> float | None:
    """
    개별 종목의 20일 수익률(stock_return_20d)에서, 같은 기간 동안 벤치마크(예: KOSPI)가 낸
    20일 수익률을 빼서 초과수익을 계산합니다.

    signal.build_event_timeline과 동일한 진입일 산정 로직(config.entry_timing에 따른
    t+1/t+2, 거래일 오프셋)을 벤치마크 가격 시계열에도 그대로 적용해, 종목 수익률 계산 때와
    동일한 기준으로 벤치마크 진입일을 맞춥니다 (apples-to-apples). 이 함수는 pead 패키지의
    다른 계산 함수들과 동일하게 가격 API를 직접 호출하지 않고 주입받은 시계열만 사용합니다.

    Args:
        event_symbol: 로깅/추적용 식별자(계산에는 사용되지 않음).
        rcept_dt: 이벤트(공시) 접수일 (t=0).
        stock_return_20d: build_event_timeline으로 이미 계산된 종목의 20일 수익률.
        benchmark_price_series: {날짜(YYYYMMDD 문자열): 종가} 형태의 pd.Series (인덱스가
                                 YYYYMMDD 문자열). rcept_dt 이후 구간의 거래일이 포함되어
                                 있어야 합니다.
        config: config.entry_timing('t+1' 또는 't+2')에 따라 벤치마크 진입 시점도 결정됩니다.

    Returns:
        초과수익률(stock_return_20d - benchmark_return_20d), 또는 벤치마크 쪽에서 20일
        수익률을 계산할 수 없으면(거래일 부족 등) None. 계산 불가를 조용히 0으로
        대체하지 않습니다.
    """
    benchmark_timeline = build_event_timeline(
        event_symbol, rcept_dt, benchmark_price_series.to_dict(), config
    )
    if benchmark_timeline is None or benchmark_timeline["return_20d"] is None:
        return None

    return stock_return_20d - benchmark_timeline["return_20d"]
