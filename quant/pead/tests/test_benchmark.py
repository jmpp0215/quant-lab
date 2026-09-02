import pandas as pd
import pytest

from quant.pead.benchmark import calculate_excess_return
from quant.pead.config import PeadConfig


def _make_benchmark_series(n_days=25, start_day=1, base=1000.0, step=10.0):
    """rcept_dt='20230101' 기준 연속된 날짜의 mock 벤치마크(예: KOSPI) 종가 시계열."""
    dates = [f"202301{str(start_day + i).zfill(2)}" for i in range(n_days)]
    prices = [base + step * i for i in range(n_days)]
    return pd.Series(prices, index=dates)


def test_calculate_excess_return_basic():
    # entry_timing='t+1' -> entry_price = 시리즈의 index 1 (1010), 20일 후 = index 21 (1210)
    config = PeadConfig(entry_timing="t+1")
    benchmark_price_series = _make_benchmark_series()

    stock_return_20d = 0.25
    excess = calculate_excess_return(
        "005930", "20230101", stock_return_20d, benchmark_price_series, config
    )

    expected_benchmark_return = (1210.0 - 1010.0) / 1010.0
    assert excess == pytest.approx(stock_return_20d - expected_benchmark_return)


def test_calculate_excess_return_respects_entry_timing():
    # entry_timing='t+2'로 바뀌면 벤치마크 진입일도 함께 t+2로 이동해야 한다 (apples-to-apples).
    config = PeadConfig(entry_timing="t+2")
    benchmark_price_series = _make_benchmark_series()

    stock_return_20d = 0.0
    excess = calculate_excess_return(
        "005930", "20230101", stock_return_20d, benchmark_price_series, config
    )

    # entry_price = index 2 (1020), 20일 후 = index 22 (1220)
    expected_benchmark_return = (1220.0 - 1020.0) / 1020.0
    assert excess == pytest.approx(stock_return_20d - expected_benchmark_return)


def test_calculate_excess_return_none_when_benchmark_data_insufficient():
    # 벤치마크 시리즈에 20거래일 후 데이터가 없으면(거래일 5개뿐) 조용히 0을 반환하는 대신
    # None을 반환해 "계산 불가"임을 알려야 한다.
    config = PeadConfig(entry_timing="t+1")
    benchmark_price_series = _make_benchmark_series(n_days=5)

    excess = calculate_excess_return(
        "005930", "20230101", 0.10, benchmark_price_series, config
    )
    assert excess is None
