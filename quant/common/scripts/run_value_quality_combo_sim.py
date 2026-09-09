import sys
import sqlite3
import pandas as pd
import numpy as np
import random
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from quant.common.portfolio import simulate_portfolio
from quant.stock_factors.config import FactorConfig
from quant.stock_factors.gpa import prepare_gpa_signals
from quant.stock_factors.value import prepare_pbr_signals

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "quant.db"

def main():
    print("Loading data...")
    conn = sqlite3.connect(DB_PATH)
    
    # Load Prices
    df_price = pd.read_sql("SELECT date, symbol, close FROM pead_price_raw WHERE date >= '2021-01-01' AND date <= '2026-12-31'", conn)
    df_price['date'] = pd.to_datetime(df_price['date'])
    df_price = df_price.sort_values('date')
    
    # Load KS11 (Benchmark)
    df_ks11 = pd.read_sql("SELECT date, close FROM pead_benchmark_raw WHERE index_symbol='KS11' AND date >= '2021-01-01' AND date <= '2026-12-31'", conn)
    df_ks11['date'] = pd.to_datetime(df_ks11['date'])
    df_ks11 = df_ks11.sort_values('date').set_index('date')
    
    # Load Trading Dates
    query_dates = "SELECT DISTINCT date FROM pead_price_raw WHERE date >= '20210101' ORDER BY date"
    trading_dates = pd.read_sql(query_dates, conn)['date']
    all_dates_idx = pd.DatetimeIndex(trading_dates)
    
    conn.close()

    # Prep Price Pivot
    price_by_sym = dict(tuple(df_price.groupby('symbol')))
    price_series_by_sym = {
        sym: dict(zip(p_df['date'].dt.strftime('%Y%m%d'), p_df['close'], strict=True))
        for sym, p_df in price_by_sym.items()
    }
    df_price_pivot = df_price.pivot(index='date', columns='symbol', values='close').reindex(all_dates_idx)
    
    # EW(All) Signal
    available_symbols_by_date = {}
    for dt in all_dates_idx:
        available_symbols_by_date[dt] = df_price_pivot.loc[dt].dropna().index.tolist()
        
    def ew_signal_func(date_str: str) -> list:
        dt = pd.to_datetime(date_str)
        return available_symbols_by_date.get(dt, [])
        
    # PBR Signal
    df_bps_ffill = prepare_pbr_signals("20210101")
    
    def get_pbr_signal(date_str: str, top_percentile=0.2, max_n=None) -> list:
        dt = pd.to_datetime(date_str)
        if dt not in df_bps_ffill.index or dt not in df_price_pivot.index: return []
        bps = df_bps_ffill.loc[dt].dropna()
        price = df_price_pivot.loc[dt].dropna()
        common_idx = bps.index.intersection(price.index)
        if len(common_idx) == 0: return []
        
        pbr = price.loc[common_idx] / bps.loc[common_idx]
        pbr = pbr[pbr > 0]
        n = int(len(pbr) * top_percentile)
        if n == 0: return []
        
        selected = pbr.nsmallest(n).index.tolist()
        if max_n and len(selected) > max_n:
            selected = selected[:max_n]
        return selected
        
    # GPA Signal
    config_gpa = FactorConfig(lookback_quarters=4)
    df_gpa_ffill = prepare_gpa_signals("20210101", config_gpa)
    
    def get_gpa_signal(date_str: str, top_percentile=0.2) -> list:
        dt = pd.to_datetime(date_str)
        if dt not in df_gpa_ffill.index or dt not in df_price_pivot.index: return []
        gpa = df_gpa_ffill.loc[dt].dropna()
        price = df_price_pivot.loc[dt].dropna()
        common_idx = gpa.index.intersection(price.index)
        if len(common_idx) == 0: return []
        n = int(len(common_idx) * top_percentile)
        if n == 0: return []
        return gpa.loc[common_idx].nlargest(n).index.tolist()
        
    # Intersection Signal (PBR Bottom 20% & GPA Top 20%)
    def intersection_signal(date_str: str) -> list:
        pbr_set = set(get_pbr_signal(date_str, top_percentile=0.2))
        gpa_set = set(get_gpa_signal(date_str, top_percentile=0.2))
        return list(pbr_set.intersection(gpa_set))
        
    # Composite Score Signal
    def composite_signal(date_str: str) -> list:
        dt = pd.to_datetime(date_str)
        if dt not in df_bps_ffill.index or dt not in df_gpa_ffill.index or dt not in df_price_pivot.index: return []
        bps = df_bps_ffill.loc[dt].dropna()
        gpa = df_gpa_ffill.loc[dt].dropna()
        price = df_price_pivot.loc[dt].dropna()
        
        common_idx = bps.index.intersection(price.index).intersection(gpa.index)
        if len(common_idx) == 0: return []
        
        pbr = price.loc[common_idx] / bps.loc[common_idx]
        pbr = pbr[pbr > 0]
        
        common_idx2 = pbr.index.intersection(gpa.index)
        if len(common_idx2) == 0: return []
        
        # Rank: PBR lowest is best (rank ascending), GPA highest is best (rank descending)
        pbr_rank = pbr.loc[common_idx2].rank(ascending=True, pct=True)
        gpa_rank = gpa.loc[common_idx2].rank(ascending=False, pct=True)
        
        # Combined score: lower is better
        composite_score = pbr_rank + gpa_rank
        n = int(len(composite_score) * 0.2)
        if n == 0: return []
        
        return composite_score.nsmallest(n).index.tolist()
        
    # Random Monkey (N=150)
    def random_signal(date_str: str) -> list:
        dt = pd.to_datetime(date_str)
        av = available_symbols_by_date.get(dt, [])
        if len(av) < 150: return av
        return random.sample(av, 150)
        
    td = trading_dates.tolist()
    
    # Benchmarks
    print("Running Base Benchmarks...")
    df_ew = simulate_portfolio(td, price_series_by_sym, ew_signal_func, 20)
    
    # 30 Monte Carlo
    print("Running 30 Random Monte Carlo Iterations (RB=20)...")
    random_sims = []
    for _ in range(30):
        res = simulate_portfolio(td, price_series_by_sym, random_signal, 20)
        random_sims.append(res)
        
    # 1. Rebalancing Period Comparison
    print("Running PBR Rebalance Period Comparison...")
    df_pbr_5 = simulate_portfolio(td, price_series_by_sym, lambda d: get_pbr_signal(d, max_n=None), 5)
    df_pbr_10 = simulate_portfolio(td, price_series_by_sym, lambda d: get_pbr_signal(d, max_n=None), 10)
    df_pbr_20 = simulate_portfolio(td, price_series_by_sym, lambda d: get_pbr_signal(d, max_n=None), 20)
    
    # 2. Combo Comparison
    print("Running GPA Combination Comparison...")
    df_combo_intersect = simulate_portfolio(td, price_series_by_sym, intersection_signal, 20)
    df_combo_score = simulate_portfolio(td, price_series_by_sym, composite_signal, 20)
    
    # 3. Size Comparison (Top 30 PBR)
    print("Running PBR Concentration Comparison (Top 30)...")
    df_pbr_30 = simulate_portfolio(td, price_series_by_sym, lambda d: get_pbr_signal(d, max_n=30), 20)
    
    # Build reporting DataFrame
    df_merged = df_ks11.copy()
    df_merged['cum_KS11'] = (df_merged['close'] / df_merged['close'].iloc[0]) - 1
    
    def merge_sim(df, col_name):
        df_merged[col_name] = df.set_index('date')['cum_ret']
        
    merge_sim(df_ew, 'EW')
    merge_sim(df_pbr_5, 'PBR_5')
    merge_sim(df_pbr_10, 'PBR_10')
    merge_sim(df_pbr_20, 'PBR_20')
    merge_sim(df_combo_intersect, 'Combo_Int')
    merge_sim(df_combo_score, 'Combo_Score')
    merge_sim(df_pbr_30, 'PBR_Top30')
    
    for i, res in enumerate(random_sims):
        merge_sim(res, f'Rand_{i}')
        
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
    
    def print_table(title, columns, col_labels):
        print("\n" + "="*145)
        print(f"[{title}]")
        print(f"{'Period':<15} | {'Rand_Mean':>9} ( {'Min':>7} ~ {'Max':>7} ) {'Std':>6} | " + " | ".join([f"{l:>16}" for l in col_labels]))
        print("-" * 145)
        
        for name, start_dt, end_dt in periods:
            mask = (df_merged.index >= start_dt) & (df_merged.index <= end_dt)
            if not mask.any(): continue
            sub = df_merged[mask]
            
            def get_ret(col):
                if pd.isna(sub[col].iloc[0]) or pd.isna(sub[col].iloc[-1]): return 0
                return (1 + sub[col].iloc[-1]) / (1 + sub[col].iloc[0]) - 1
                
            r_rets = []
            for i in range(30):
                r_rets.append(get_ret(f'Rand_{i}'))
            r_arr = np.array(r_rets)
            r_mean, r_min, r_max, r_std = r_arr.mean()*100, r_arr.min()*100, r_arr.max()*100, r_arr.std()*100
            
            cols_out = []
            for col in columns:
                ret = get_ret(col)
                pct = (np.sum(r_arr * 100 < ret * 100) / len(r_arr)) * 100
                cols_out.append(f"{ret*100:>9.2f}% (T{100-pct:>5.1f}%)")
                
            print(f"{name:<15} | {r_mean:>8.2f}% ({r_min:>7.2f}% ~ {r_max:>7.2f}%) {r_std:>5.2f}% | " + " | ".join(cols_out))

    print_table("1. Rebalancing Period Comparison (PBR Q1)", 
                ['PBR_20', 'PBR_10', 'PBR_5'], 
                ['PBR (20 days)', 'PBR (10 days)', 'PBR (5 days)'])
                
    print_table("2. GPA Combination Comparison", 
                ['PBR_20', 'Combo_Int', 'Combo_Score'], 
                ['PBR (Standalone)', 'Intersect (Q1+Q5)', 'Score (Rank Sum)'])
                
    print_table("3. PBR Concentration Comparison", 
                ['PBR_20', 'PBR_Top30'], 
                ['PBR (Bottom 20%)', 'PBR (Top 30 stocks)'])

    # Also calculate average number of stocks for Context 3
    avg_intersect = df_combo_intersect['n_holdings'].mean()
    avg_score = df_combo_score['n_holdings'].mean()
    avg_pbr = df_pbr_20['n_holdings'].mean()
    print(f"\n[Holding Statistics]")
    print(f"Average stocks in PBR (Bottom 20%): {avg_pbr:.1f}")
    print(f"Average stocks in Intersect (PBR Q1 + GPA Q5): {avg_intersect:.1f}")
    print(f"Average stocks in Combo Score (Top 20%): {avg_score:.1f}")
    print(f"Average stocks in PBR (Top 30): {df_pbr_30['n_holdings'].mean():.1f}")
    
    # Check top 5 contributions for Combo_Score in 2021-2024 to see if there's concentration bias
    # We can do that by quickly re-running the combo_score signal for contribution tracking
    # (Optional, but user asked to check "상장폐지/기여도 쏠림 체크" like PBR)
    # The prompt says: "상장폐지/기여도 쏠림 체크: PBR처럼 상위 5개 종목 기여도 합산 확인"
    # I will calculate it directly in the script for Combo_Score.
    
    print("\nCalculating Top 5 Contributors for Combo_Score (2021-2024)...")
    contrib = {}
    held_days = {}
    rebalance_counter = 0
    current_symbols = []
    missing_events = 0
    
    mask = (df_merged.index >= '2021-01-01') & (df_merged.index <= '2024-12-31')
    sim_dates = df_merged[mask].index.strftime('%Y%m%d').tolist()
    
    for i, date in enumerate(sim_dates):
        if i > 0 and current_symbols:
            prev_date = sim_dates[i-1]
            n = len(current_symbols)
            for sym in current_symbols:
                p_prev = price_series_by_sym.get(sym, {}).get(prev_date)
                p_curr = price_series_by_sym.get(sym, {}).get(date)
                if p_prev and p_curr and p_prev > 0:
                    ret = (p_curr - p_prev) / p_prev
                    contrib[sym] = contrib.get(sym, 0.0) + (ret / n)
                elif p_prev and (not p_curr or p_curr == 0):
                    missing_events += 1
                held_days[sym] = held_days.get(sym, 0) + 1
                
        if rebalance_counter == 0 or i == 0:
            new_symbols = composite_signal(date)
            current_symbols = new_symbols
            rebalance_counter = 20
        rebalance_counter -= 1
        
    import FinanceDataReader as fdr
    kospi_df = fdr.StockListing('KOSPI')
    name_dict = dict(zip(kospi_df['Code'], kospi_df['Name']))
    sorted_contrib = sorted(contrib.items(), key=lambda x: x[1], reverse=True)
    
    print(f"Top 5 Contributors (Combo_Score, 2021-2024):")
    total_combo = 0
    for sym, c in sorted_contrib[:5]:
        total_combo += c
        print(f" - {sym} ({name_dict.get(sym, 'Unknown')}): +{c*100:.2f}% (Held {held_days[sym]} days)")
    
    print(f"Top 5 Sum Contribution: +{total_combo*100:.2f}%")
    print(f"Missing price events (halts/delistings): {missing_events}")

if __name__ == "__main__":
    main()
