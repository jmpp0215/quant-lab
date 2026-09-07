import os
import logging
from datetime import date
from decimal import Decimal
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path

from quant.stock_factors.value import prepare_pbr_signals, get_pbr_signal_func
from quant.rebalance import Order, Position
from quant.market import round_to_tick
import FinanceDataReader as fdr

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "data" / "quant.db"

# Refuse to build a target portfolio when pead_price_raw is this many trading
# days (weekends excluded, KRX holidays not) or more behind as_of_date - a
# stale price table would otherwise silently rank symbols on old data.
MAX_STALE_TRADING_DAYS = 2

class PBRConfig:
    def __init__(self, target_n_stocks: int = 50, rebalance_days: int = 20):
        self.target_n_stocks = target_n_stocks
        self.rebalance_days = rebalance_days
        self.dry_run = os.getenv("DRY_RUN", "true").lower() != "false"

def get_latest_prices(date_str: str) -> dict[str, Decimal]:
    """Fetch prices as of the given date (or the most recent trading day)."""
    # pead_price_raw stores dates as YYYYMMDD; strip any dashes so the
    # comparison below isn't comparing against the wrong lexical ordering
    # (e.g. "2026-09-04" sorts before "20260902" - see latest_price_date()).
    date_key = date_str.replace("-", "")
    conn = sqlite3.connect(DB_PATH)
    # Get the exact or most recent date up to date_str
    query = f"""
        SELECT symbol, close
        FROM pead_price_raw
        WHERE date = (
            SELECT MAX(date) FROM pead_price_raw WHERE date <= '{date_key}'
        )
    """
    df = pd.read_sql(query, conn)
    conn.close()

    return {row['symbol']: Decimal(str(row['close'])) for _, row in df.iterrows()}


def latest_price_date() -> str | None:
    """Most recent date (YYYYMMDD) present in pead_price_raw, or None if empty."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT MAX(date) FROM pead_price_raw").fetchone()
    conn.close()
    return row[0]


def price_data_is_stale(as_of_date: str, latest: str | None,
                        max_trading_days: int = MAX_STALE_TRADING_DAYS) -> bool:
    """True if latest is more than max_trading_days behind as_of_date.

    Trading-day gap (numpy.busday_count - weekends excluded, KRX holidays
    not) rather than calendar days, so an ordinary Friday-cached/Monday-run
    gap doesn't false-positive as stale.
    """
    if latest is None:
        return True
    as_of_key = as_of_date.replace("-", "")
    latest_dt = date.fromisoformat(f"{latest[:4]}-{latest[4:6]}-{latest[6:]}")
    as_of_dt = date.fromisoformat(f"{as_of_key[:4]}-{as_of_key[4:6]}-{as_of_key[6:]}")
    gap = int(np.busday_count(latest_dt, as_of_dt))
    return gap > max_trading_days

def construct_target_portfolio(as_of_date: str, config: PBRConfig) -> dict[str, Decimal]:
    """
    Calculate the target portfolio weights for the given date.
    Returns: dict mapping symbol to target weight (Decimal).
    """
    as_of_date_stripped = as_of_date.replace("-", "")
    
    # We load BPS data starting from the year of as_of_date for performance, 
    # but to be safe with ffill, we load from start of previous year.
    year = int(as_of_date_stripped[:4])
    start_fetch_date = f"{year-1}0101"
    
    # 1. Prepare Signals
    df_bps = prepare_pbr_signals(start_fetch_date)
    
    # 2. Get Universe Snapshot prices and volume
    conn = sqlite3.connect(DB_PATH)
    df_price = pd.read_sql(
        f"SELECT date, symbol, close, volume FROM pead_price_raw "
        f"WHERE date >= '{start_fetch_date}' AND date <= '{as_of_date_stripped}'",
        conn,
    )
    conn.close()
    
    df_price['date'] = pd.to_datetime(df_price['date'])
    df_price = df_price.sort_values('date')
    
    trading_dates = sorted(df_price['date'].dt.strftime('%Y%m%d').unique())
    all_dates_idx = pd.to_datetime(trading_dates)
    df_price_pivot = df_price.pivot(index='date', columns='symbol', values='close').reindex(all_dates_idx)
    df_vol_pivot = df_price.pivot(index='date', columns='symbol', values='volume').reindex(all_dates_idx)
    
    # Calculate Suspension Filter (20-day zero volume)
    df_susp_20 = (df_vol_pivot == 0).astype(int).rolling(window=20, min_periods=1).max()
    
    # Find the most recent available trading date <= as_of_date_stripped
    valid_dates = [d for d in trading_dates if d <= as_of_date_stripped]
    if not valid_dates:
        return {}
    target_date_str = valid_dates[-1]
    
    # 3. Apply Signal Function
    base_func = get_pbr_signal_func(df_bps, df_price_pivot, top_percentile=0.2)
    selected_symbols = base_func(target_date_str)
    
    # Apply Suspension Filter
    target_dt = pd.to_datetime(target_date_str)
    if target_dt in df_susp_20.index:
        susp_row = df_susp_20.loc[target_dt]
        valid_symbols = susp_row[susp_row == 0].index
        selected_symbols = [sym for sym in selected_symbols if sym in valid_symbols]
    
    # Slice to target concentration
    if config.target_n_stocks and len(selected_symbols) > config.target_n_stocks:
        selected_symbols = selected_symbols[:config.target_n_stocks]
        
    if not selected_symbols:
        return {}
        
    weight = Decimal("1.0") / Decimal(len(selected_symbols))
    return {sym: weight for sym in selected_symbols}

def calculate_diff(target_weights: dict[str, Decimal], 
                   positions: dict[str, Position], 
                   prices: dict[str, Decimal], 
                   cash: Decimal) -> list[Order]:
    """
    Compare current positions with target weights and generate Orders.
    This logic mimics quant/rebalance.py's plan().
    """
    total = cash + sum(p.value for p in positions.values())
    
    sells: list[Order] = []
    buys: list[Order] = []
    
    symbols = set(positions) | set(target_weights)
    
    kospi_df = fdr.StockListing('KOSPI')
    name_dict = dict(zip(kospi_df['Code'], kospi_df['Name']))
    
    # Minimum order value threshold (can be configured)
    MIN_ORDER_KRW = Decimal("50000")
    
    for symbol in sorted(symbols):
        price = prices.get(symbol)
        if price is None and symbol in positions:
            price = positions[symbol].last_price
            
        if price is None or price <= 0:
            log.warning("%s: no price available, skipping", symbol)
            continue
            
        weight = target_weights.get(symbol, Decimal("0"))
        target_qty = int(total * weight / price)
        held_qty = positions[symbol].quantity if symbol in positions else 0
        delta = target_qty - held_qty
        
        if delta == 0:
            continue
            
        name = name_dict.get(symbol, positions[symbol].name if symbol in positions else symbol)
        
        # Determine tick size (assume individual stocks, not ETF)
        limit = round_to_tick(price, is_etf=False)
        
        order = Order(
            symbol=symbol,
            name=name,
            side="BUY" if delta > 0 else "SELL",
            quantity=abs(delta),
            limit_price=limit,
        )
        
        if order.notional < MIN_ORDER_KRW:
            continue
            
        if delta > 0:
            buys.append(order)
        else:
            sells.append(order)
            
    # Return sells first, then buys (to free up cash)
    return sells + buys

def format_orders(orders: list[Order]) -> str:
    if not orders:
        return "No orders needed."
    lines = ["Target Orders (DRY_RUN):"]
    for o in orders:
        lines.append(
            f"  {o.side:<4} {o.name:<24} {o.symbol:<6} {o.quantity:>5} @ "
            f"{o.limit_price:>10,.0f} = {o.notional:>12,.0f} KRW"
        )
    return "\n".join(lines)
