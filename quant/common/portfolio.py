import pandas as pd
import numpy as np
from typing import Dict, List, Callable
import logging

log = logging.getLogger(__name__)

def simulate_portfolio(
    trading_dates: List[str],
    price_series_by_sym: Dict[str, Dict[str, float]],
    signal_func: Callable[[str], List[str]],
    rebalance_days: int = 20,
    transaction_cost: float = 0.0000  # Can be added later
) -> pd.DataFrame:
    """
    시계열 포트폴리오 시뮬레이터.
    - 리밸런싱 방식: 전체 재구성 (Full Rebalance). 
      구현이 가장 직관적이고, 각 리밸런싱 시점에 정확히 해당 시점의 신호 기준 Top N 종목에 
      동일 비중(Equal Weight)으로 노출됨을 보장하므로 요인(Factor) 검증 원칙에 가장 부합합니다.
      (종목별 교체 방식은 과거 편입 종목의 비중이 시장 수익률에 따라 왜곡되는 Drift 현상을 수반함)
      
    Args:
        trading_dates: 시뮬레이션을 진행할 오름차순 영업일 리스트 (YYYYMMDD)
        price_series_by_sym: {symbol: {date: close_price}}
        signal_func: date(YYYYMMDD)를 받아 매수할 종목(symbol) 리스트를 반환하는 함수
        rebalance_days: 리밸런싱 주기 (영업일 기준)
        
    Returns:
        일별 포트폴리오 가치 및 수익률을 담은 DataFrame
    """
    if not trading_dates:
        return pd.DataFrame()
        
    portfolio_history = []
    
    current_holdings = [] # List of symbols currently held
    portfolio_value = 1.0 # Base value 1.0
    
    # Track the number of shares (normalized to portfolio_value) for each held symbol
    # to accurately simulate daily mark-to-market.
    # shares = weight * portfolio_value / price
    held_shares = {} 
    
    rebalance_counter = 0
    
    for date in trading_dates:
        # 1. Mark-to-market current holdings
        if held_shares:
            daily_value = 0.0
            valid_holdings = 0
            for sym, shares in held_shares.items():
                price = price_series_by_sym.get(sym, {}).get(date)
                if price is not None:
                    daily_value += shares * price
                    valid_holdings += 1
                else:
                    # If price is missing (e.g. trading halt), we assume price is unchanged from yesterday.
                    # Since we don't track yesterday's price explicitly in held_shares, 
                    # a proper implementation would need it. 
                    # For simplicity in this backtest, we assume the symbol holds its value contribution.
                    pass # Handled below by a simpler approach
                    
            # Simpler Mark-to-Market: using daily returns of held symbols
            pass
            
    # Let's rewrite MTM to be robust using daily returns
    # We will compute daily returns of the portfolio.
    
    # We will rebuild it cleanly.
    daily_returns = []
    current_symbols = []
    
    for i, date in enumerate(trading_dates):
        # Calculate today's return BEFORE rebalancing (using yesterday's target symbols)
        if i > 0 and current_symbols:
            prev_date = trading_dates[i-1]
            day_returns = []
            for sym in current_symbols:
                p_prev = price_series_by_sym.get(sym, {}).get(prev_date)
                p_curr = price_series_by_sym.get(sym, {}).get(date)
                
                if p_prev and p_curr and p_prev > 0:
                    day_returns.append((p_curr - p_prev) / p_prev)
                else:
                    # Trading halt or missing data -> 0 return
                    day_returns.append(0.0)
                    
            port_ret = np.mean(day_returns) if day_returns else 0.0
        else:
            port_ret = 0.0
            
        # Rebalance at the end of the day (so tomorrow gets the new returns)
        if rebalance_counter == 0 or i == 0:
            new_symbols = signal_func(date)
            current_symbols = new_symbols
            rebalance_counter = rebalance_days
            
        rebalance_counter -= 1
        
        daily_returns.append({
            'date': date,
            'daily_ret': port_ret,
            'n_holdings': len(current_symbols)
        })
        
    df = pd.DataFrame(daily_returns)
    df['date'] = pd.to_datetime(df['date'])
    df['cum_ret'] = (1 + df['daily_ret']).cumprod() - 1
    
    return df

def calculate_mdd(cum_ret_series: pd.Series) -> float:
    """Calculate Maximum Drawdown from a cumulative return series."""
    wealth = 1 + cum_ret_series
    peaks = wealth.cummax()
    drawdowns = (wealth - peaks) / peaks
    return drawdowns.min()

def calculate_volatility(daily_ret_series: pd.Series, annual_factor: int = 252) -> float:
    """Calculate annualized volatility."""
    return daily_ret_series.std() * np.sqrt(annual_factor)
