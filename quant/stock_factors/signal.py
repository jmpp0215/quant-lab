import pandas as pd
from typing import Dict, Optional, Tuple, List
from .config import FactorConfig

def calculate_momentum_score(
    symbol: str, 
    rebalance_date: str, 
    price_series: Dict[str, float], 
    config: FactorConfig
) -> Tuple[Optional[float], bool]:
    """
    Calculates the relative momentum score (rate of return) over the past `lookback_months`.
    Uses trading days to approximate months (1 month ~ 21 trading days).
    
    Args:
        symbol: The stock symbol
        rebalance_date: The date to calculate momentum for (YYYYMMDD)
        price_series: Dict of {YYYYMMDD: close_price} for the symbol
        config: FactorConfig specifying lookback_months
        
    Returns:
        (momentum_score, is_estimable)
    """
    # Filter dates up to rebalance_date to ensure point-in-time
    trading_dates = sorted(d for d in price_series if d <= rebalance_date)
    
    if not trading_dates:
        return None, False
        
    current_date = trading_dates[-1]
    # We require the current_date to be exactly the rebalance_date or very close
    # If the last traded date is more than 5 days old, it might be suspended/delisted
    # But for simplicity, we just use the latest available up to rebalance_date.
    
    # Approx trading days for lookback
    lookback_days = config.lookback_months * 21
    
    if len(trading_dates) <= lookback_days:
        return None, False
        
    past_date = trading_dates[-(lookback_days + 1)]
    
    current_price = price_series[current_date]
    past_price = price_series[past_date]
    
    if past_price == 0:
        return None, False
        
    momentum = (current_price - past_price) / past_price
    
    return momentum, True

def cross_sectional_ranking(
    scores: Dict[str, float], 
    quantiles: int = 10
) -> Dict[str, int]:
    """
    Ranks the given cross-sectional scores into quantiles (default deciles).
    1 = Lowest Score (Losers), quantiles = Highest Score (Winners).
    
    Args:
        scores: Dict of {symbol: momentum_score}
        quantiles: Number of quantiles (e.g., 10 for deciles)
        
    Returns:
        Dict of {symbol: quantile_rank (1 to quantiles)}
    """
    if not scores:
        return {}
        
    df = pd.DataFrame(list(scores.items()), columns=['symbol', 'score'])
    
    # Use qcut to divide into quantiles. labels are 1 to quantiles.
    try:
        df['rank'] = pd.qcut(df['score'].rank(method='first'), q=quantiles, labels=range(1, quantiles + 1))
    except ValueError:
        # If not enough data points for quantiles
        return {}
        
    return dict(zip(df['symbol'], df['rank']))
