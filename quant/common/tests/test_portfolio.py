import pytest
import pandas as pd
from quant.common.portfolio import simulate_portfolio, calculate_mdd

def test_simulate_portfolio():
    # Mock trading dates
    trading_dates = ["20230101", "20230102", "20230103", "20230104", "20230105"]
    
    # Mock prices for 2 symbols
    # A goes 100 -> 110 -> 121 -> 133.1 -> 146.41 (10% daily)
    # B goes 100 -> 90 -> 81 -> 72.9 -> 65.61 (-10% daily)
    price_series = {
        "A": {"20230101": 100.0, "20230102": 110.0, "20230103": 121.0, "20230104": 133.1, "20230105": 146.41},
        "B": {"20230101": 100.0, "20230102": 90.0,  "20230103": 81.0,  "20230104": 72.9,  "20230105": 65.61}
    }
    
    # Mock signal: always select A and B (equal weight)
    def mock_signal(date):
        return ["A", "B"]
        
    df = simulate_portfolio(trading_dates, price_series, mock_signal, rebalance_days=2)
    
    assert len(df) == 5
    
    # Day 1 (0101): no previous day, return is 0
    assert df.iloc[0]['daily_ret'] == 0.0
    
    # Day 2 (0102): A is +10%, B is -10%. Equal weight average return = 0%
    assert df.iloc[1]['daily_ret'] == 0.0
    
    # Day 3 (0103): Rebalance happened at end of Day 1 (but wait, rebalance_counter=2).
    # i=0: rebalance counter=0 -> triggers rebalance. Current symbols = [A, B]. counter becomes 2.
    # i=0 end: counter becomes 1.
    # i=1 (0102): no rebalance. counter becomes 0.
    # i=2 (0103): counter=0 -> triggers rebalance.
    
    # Let's check Day 3 return. A is +10%, B is -10%. Again, return = 0%
    # (Since it's full rebalance, weights are reset to 50/50, so return is exactly 0).
    # Wait, the code calculates `np.mean(day_returns)` which is explicitly equal weight.
    # So every day the return is 0%.
    assert df.iloc[2]['daily_ret'] == pytest.approx(0.0)
    assert df.iloc[-1]['cum_ret'] == pytest.approx(0.0)

def test_simulate_portfolio_dynamic():
    trading_dates = ["20230101", "20230102", "20230103"]
    price_series = {
        "A": {"20230101": 100.0, "20230102": 110.0, "20230103": 121.0}, # +10%, +10%
        "B": {"20230101": 100.0, "20230102": 90.0,  "20230103": 81.0}   # -10%, -10%
    }
    
    # Signal: buy A on day 1, buy B on day 2
    def mock_signal(date):
        if date == "20230101": return ["A"]
        if date == "20230102": return ["B"]
        return []
        
    df = simulate_portfolio(trading_dates, price_series, mock_signal, rebalance_days=1)
    
    # Day 1: ret=0
    # Day 2: held A. A went from 100->110 (+10%). ret=0.1
    assert df.iloc[1]['daily_ret'] == pytest.approx(0.1)
    # Day 3: held B. B went from 90->81 (-10%). ret=-0.1
    assert df.iloc[2]['daily_ret'] == pytest.approx(-0.1)
    
    # Cum ret: (1.1) * (0.9) - 1 = 0.99 - 1 = -0.01
    assert df.iloc[-1]['cum_ret'] == pytest.approx(-0.01)
    
def test_calculate_mdd():
    # Wealth: 1, 1.1, 0.99, 1.2
    cum_rets = pd.Series([0.0, 0.1, -0.01, 0.2])
    # Peaks: 1, 1.1, 1.1, 1.2
    # Drawdowns: 0, 0, (0.99-1.1)/1.1 = -0.1/1.1 = -0.0909..., 0
    mdd = calculate_mdd(cum_rets)
    assert mdd == pytest.approx(-0.1)
