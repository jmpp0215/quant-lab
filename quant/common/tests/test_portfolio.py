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
    
    # After the first day A holds 55% and B 45%. Reset after day 3.
    assert df.iloc[2]['daily_ret'] == pytest.approx(0.01)
    assert df.iloc[-1]['cum_ret'] == pytest.approx(1.01 ** 2 - 1)


def test_cost_charged_on_entry_rotation_and_exit():
    dates = ["20230101", "20230102", "20230103"]
    prices = {s: dict.fromkeys(dates, 100) for s in ("A", "B")}
    target = dict(zip(dates, [["A"], ["B"], []], strict=True))
    result = simulate_portfolio(dates, prices, target.get, 1, transaction_cost=0.01)
    assert result.daily_ret.tolist() == pytest.approx([-0.01, -0.02, -0.01])


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
    
def test_simulate_portfolio_ignores_price_anomaly():
    # B has a real +10%/day trend, but a corporate-action artifact (unadjusted
    # 15:1-style reverse split) fakes a +1000% jump on day 3 that must not
    # flow into the portfolio return.
    trading_dates = ["20230101", "20230102", "20230103", "20230104"]
    price_series = {
        "A": {"20230101": 100.0, "20230102": 100.0, "20230103": 100.0, "20230104": 100.0},
        "B": {"20230101": 100.0, "20230102": 110.0, "20230103": 1210.0, "20230104": 1331.0},
    }

    def mock_signal(date):
        return ["A", "B"]

    df = simulate_portfolio(trading_dates, price_series, mock_signal, rebalance_days=10)

    # Day 3: A flat (0%), B's move is flagged and treated as 0% -> portfolio return 0%
    assert df.iloc[2]['daily_ret'] == pytest.approx(0.0)
    # Day 4: A flat, B genuinely +10% (1210 -> 1331) at its drifted weight
    assert df.iloc[3]['daily_ret'] == pytest.approx(0.1 * 55 / 105)


def test_calculate_mdd():
    # Wealth: 1, 1.1, 0.99, 1.2
    cum_rets = pd.Series([0.0, 0.1, -0.01, 0.2])
    # Peaks: 1, 1.1, 1.1, 1.2
    # Drawdowns: 0, 0, (0.99-1.1)/1.1 = -0.1/1.1 = -0.0909..., 0
    mdd = calculate_mdd(cum_rets)
    assert mdd == pytest.approx(-0.1)
