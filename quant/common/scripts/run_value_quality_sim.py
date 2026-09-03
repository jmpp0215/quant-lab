import os
import sys
import sqlite3
import pandas as pd
import numpy as np
import random
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from quant.common.portfolio import simulate_portfolio
from quant.stock_factors.config import FactorConfig
from quant.stock_factors.gpa import prepare_gpa_signals, get_gpa_signal_func
from quant.stock_factors.value import prepare_pbr_signals, get_pbr_signal_func

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "quant.db"

def main():
    print("Loading data...")
    conn = sqlite3.connect(DB_PATH)
    
    # Load Prices (Full KOSPI Universe)
    df_price = pd.read_sql("SELECT date, symbol, close FROM pead_price_raw", conn)
    df_price['date'] = pd.to_datetime(df_price['date'])
    df_price = df_price.sort_values('date')
    
    df_bench = pd.read_sql("SELECT date, close FROM pead_benchmark_raw WHERE index_symbol = 'KS11'", conn)
    df_bench['date'] = pd.to_datetime(df_bench['date'])
    df_bench = df_bench.sort_values('date')
    df_bench['daily_ret'] = df_bench['close'].pct_change().fillna(0)
    
    price_by_sym = dict(tuple(df_price.groupby('symbol')))
    price_series_by_sym = {
        sym: dict(zip(p_df['date'].dt.strftime('%Y%m%d'), p_df['close'], strict=True))
        for sym, p_df in price_by_sym.items()
    }
    
    trading_dates = sorted(df_price['date'].dt.strftime('%Y%m%d').unique())
    trading_dates = [d for d in trading_dates if d >= "20210101"]
    all_dates_idx = pd.to_datetime(trading_dates)
    
    # Calculate KOSPI EW Return
    df_price['daily_ret'] = df_price.groupby('symbol')['close'].pct_change()
    ew_daily = df_price.groupby('date')['daily_ret'].mean().reset_index()
    ew_daily.rename(columns={'daily_ret': 'ew_ret'}, inplace=True)
    
    df_merged = df_bench[['date', 'daily_ret']].rename(columns={'daily_ret': 'ks11_ret'})
    df_merged = pd.merge(df_merged, ew_daily, on='date', how='inner')
    df_merged = df_merged[df_merged['date'] >= "2021-01-01"].set_index('date')
    
    # Price Pivot for PBR
    df_price_pivot = df_price.pivot(index='date', columns='symbol', values='close')
    df_price_pivot = df_price_pivot.reindex(all_dates_idx)
    
    # Signals
    config_gpa = FactorConfig(lookback_quarters=4) # 4 quarters rolling
    print("Preparing GPA Signals (Rolling 4Q)...")
    df_gpa_ffill = prepare_gpa_signals("20210101", config_gpa)
    gpa_signal_func = get_gpa_signal_func(df_gpa_ffill, top_percentile=0.2)
    
    print("Preparing PBR Signals (Latest Snapshot)...")
    df_bps_ffill = prepare_pbr_signals("20210101")
    pbr_signal_func = get_pbr_signal_func(df_bps_ffill, df_price_pivot, top_percentile=0.2)
    
    # Random 150
    available_symbols_by_date = {}
    for dt in all_dates_idx:
        available_symbols_by_date[dt] = df_price_pivot.loc[dt].dropna().index.tolist()
        
    def get_random_signal_func():
        def random_signal(date_str: str) -> list:
            dt = pd.to_datetime(date_str)
            av = available_symbols_by_date.get(dt, [])
            if len(av) < 150: return av
            return random.sample(av, 150)
        return random_signal

    rb = 20 # Rebalance every 20 days
    
    print("Running GPA Simulation...")
    gpa_df = simulate_portfolio(trading_dates, price_series_by_sym, gpa_signal_func, rb)
    
    print("Running PBR Simulation...")
    pbr_df = simulate_portfolio(trading_dates, price_series_by_sym, pbr_signal_func, rb)
    
    print("Running 30 Random Monte Carlo Iterations...")
    random_dfs = []
    for i in range(30):
        rdf = simulate_portfolio(trading_dates, price_series_by_sym, get_random_signal_func(), rb)
        random_dfs.append(rdf.set_index('date')['daily_ret'])
        
    df_merged['gpa_ret'] = gpa_df.set_index('date')['daily_ret']
    df_merged['pbr_ret'] = pbr_df.set_index('date')['daily_ret']
    df_merged = df_merged.fillna(0)
    
    periods = [
        ("Full 2021-2026", "2021-01-01", "2026-12-31"),
        ("2021-2024", "2021-01-01", "2024-12-31"),
        ("2025-2026", "2025-01-01", "2026-12-31"),
        ("Year 2021", "2021-01-01", "2021-12-31"),
        ("Year 2022", "2022-01-01", "2022-12-31"),
        ("Year 2023", "2023-01-01", "2023-12-31"),
        ("Year 2024", "2024-01-01", "2024-12-31"),
        ("Year 2025", "2025-01-01", "2025-12-31"),
        ("Year 2026", "2026-01-01", "2026-12-31"),
    ]
    
    print("\n" + "="*125)
    print(f"{'Period':<15} | {'KS11(MCap)':>10} | {'EW(All)':>10} | {'GPA(Q5) (Rank)':>16} | {'PBR(Q1) (Rank)':>16} | {'Rand_Mean':>9} ( {'Min':>7} ~ {'Max':>7} ) {'Std':>6}")
    print("-" * 125)
    
    for name, start_dt, end_dt in periods:
        mask = (df_merged.index >= start_dt) & (df_merged.index <= end_dt)
        if not mask.any(): continue
        
        p_df = df_merged.loc[mask]
        cum = (1 + p_df).prod() - 1
        ks = cum['ks11_ret'] * 100
        ew = cum['ew_ret'] * 100
        gpa = cum['gpa_ret'] * 100
        pbr = cum['pbr_ret'] * 100
        
        r_rets = []
        for rdf in random_dfs:
            r_ret = (1 + rdf.loc[start_dt:end_dt]).prod() - 1
            r_rets.append(r_ret)
            
        r_arr = np.array(r_rets)
        r_mean = r_arr.mean() * 100
        r_min = r_arr.min() * 100
        r_max = r_arr.max() * 100
        r_std = r_arr.std() * 100
        
        gpa_pct = (np.sum(r_arr * 100 < gpa) / len(r_arr)) * 100
        pbr_pct = (np.sum(r_arr * 100 < pbr) / len(r_arr)) * 100
        
        print(f"{name:<15} | {ks:>9.2f}% | {ew:>9.2f}% | {gpa:>9.2f}% (Top {100-gpa_pct:>4.1f}%) | {pbr:>9.2f}% (Top {100-pbr_pct:>4.1f}%) | {r_mean:>8.2f}% ({r_min:>7.2f}% ~ {r_max:>7.2f}%) {r_std:>5.2f}%")

if __name__ == "__main__":
    main()
