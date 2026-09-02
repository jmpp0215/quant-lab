import pytest
from quant.stock_factors.config import FactorConfig
from quant.stock_factors.signal import calculate_momentum_score, cross_sectional_ranking

def test_calculate_momentum_score():
    config = FactorConfig(lookback_months=3) # 3 months = ~63 days
    
    # Generate mock price series (65 days)
    # Day 1: 100, Day 65: 110. Returns should be 10%
    price_series = {}
    for i in range(1, 66):
        date_str = f"202301{i:02d}" if i <= 31 else f"202302{i-31:02d}"
        if i == 1:
            price_series[date_str] = 100.0
        elif i == 65:
            price_series[date_str] = 110.0
        else:
            price_series[date_str] = 105.0
            
    # 65 trading days. 63 days ago from day 65 is day 2.
    # Wait, lookback_days = 3 * 21 = 63.
    # Index of current date = 64 (0-indexed). Index of past date = 64 - 63 = 1 (day 2).
    # Let's adjust day 2 price to 100.0
    keys = sorted(list(price_series.keys()))
    past_date = keys[1] 
    price_series[past_date] = 100.0
    
    current_date = keys[-1]
    
    score, is_est = calculate_momentum_score("A", current_date, price_series, config)
    assert is_est is True
    assert score == pytest.approx(0.10)
    
def test_calculate_momentum_score_not_estimable():
    config = FactorConfig(lookback_months=3)
    
    # Generate mock price series with less than 63 days
    price_series = {}
    for i in range(1, 10):
        date_str = f"202301{i:02d}"
        price_series[date_str] = 100.0
        
    current_date = "20230109"
    score, is_est = calculate_momentum_score("A", current_date, price_series, config)
    assert is_est is False
    assert score is None
    
def test_cross_sectional_ranking():
    scores = {
        "A": 0.1,
        "B": 0.5,
        "C": -0.2,
        "D": 0.3,
        "E": 0.0
    }
    
    # 5 items, into quintiles (5)
    ranks = cross_sectional_ranking(scores, quantiles=5)
    
    # C (-0.2) is 1st (Lowest)
    # E (0.0) is 2nd
    # A (0.1) is 3rd
    # D (0.3) is 4th
    # B (0.5) is 5th (Highest)
    
    assert ranks["C"] == 1
    assert ranks["E"] == 2
    assert ranks["A"] == 3
    assert ranks["D"] == 4
    assert ranks["B"] == 5

def test_cross_sectional_ranking_empty():
    ranks = cross_sectional_ranking({}, quantiles=5)
    assert ranks == {}
