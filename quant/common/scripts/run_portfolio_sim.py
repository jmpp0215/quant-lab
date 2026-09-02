import os
import sys
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
import logging

sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from quant.common.portfolio import simulate_portfolio, calculate_mdd, calculate_volatility
from quant.stock_factors.config import FactorConfig
from quant.pead.config import PeadConfig

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("port_sim")

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "quant.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    
    log.info("Loading price data...")
    df_price = pd.read_sql("SELECT date, symbol, close FROM pead_price_raw", conn)
    df_price['date'] = pd.to_datetime(df_price['date'])
    df_price = df_price.sort_values('date')
    
    log.info("Loading benchmark...")
    df_bench = pd.read_sql("SELECT date, close FROM pead_benchmark_raw WHERE index_symbol = 'KS11'", conn)
    df_bench['date'] = pd.to_datetime(df_bench['date'])
    df_bench = df_bench.sort_values('date')
    df_bench['daily_ret'] = df_bench['close'].pct_change().fillna(0)
    df_bench['cum_ret'] = (1 + df_bench['daily_ret']).cumprod() - 1
    bench_by_date = df_bench.set_index('date')
    
    price_by_sym = dict(tuple(df_price.groupby('symbol')))
    price_series_by_sym = {
        sym: dict(zip(p_df['date'].dt.strftime('%Y%m%d'), p_df['close'], strict=True))
        for sym, p_df in price_by_sym.items()
    }
    
    trading_dates = sorted(df_price['date'].dt.strftime('%Y%m%d').unique())
    # Cut off 2020 for warm-up (e.g. 6-month momentum needs 6 months data)
    sim_start_date = "20210101"
    trading_dates = [d for d in trading_dates if d >= sim_start_date]
    
    # ---------------------------------------------------------------------
    # 1. SUE Signal Generator
    # ---------------------------------------------------------------------
    log.info("Preparing SUE signals (Point-in-time cross-sectional)...")
    # Fetch all SUE records
    query_pead = """
        SELECT s.symbol, s.rcept_dt as date, s.surprise_score, n.target_year, n.report_code
        FROM pead_surprises s
        JOIN pead_quarterly_normalized n
          ON s.symbol = n.symbol AND s.rcept_dt = n.rcept_dt 
          AND s.metric = n.metric AND s.basis = n.basis
        WHERE s.is_estimable=1 AND s.surprise_score IS NOT NULL
    """
    df_pead = pd.read_sql(query_pead, conn).drop_duplicates(subset=['symbol', 'date'])
    
    # SUE rankings must be relative to the (target_year, report_code) cohort.
    # To be point-in-time: At any date T, we only know the surprise_scores announced <= T.
    # So for a given cohort, the quintile thresholds shift as more companies report.
    # A simpler but fully PiT approach: We just rank the raw surprise_score of the *most recent* announcement 
    # for each symbol across ALL symbols available at time T. 
    # Wait, PEAD scores are standardized (mean=0, std=1 for each symbol).
    # Cross-sectional comparison of SUE is standard.
    
    df_pead = df_pead.sort_values('date')
    
    # Pre-calculate symbol's latest SUE at any given trading date to speed up
    # This is effectively a forward-fill of the SUE score
    log.info("Forward-filling SUE scores...")
    df_sue_pivot = df_pead.pivot(index='date', columns='symbol', values='surprise_score')
    # Reindex to trading dates
    df_sue_pivot.index = pd.to_datetime(df_sue_pivot.index)
    all_dates_idx = pd.to_datetime(trading_dates)
    df_sue_pivot = df_sue_pivot.reindex(all_dates_idx.union(df_sue_pivot.index)).sort_index()
    df_sue_ffill = df_sue_pivot.ffill().reindex(all_dates_idx)
    
    def sue_signal_func(date_str: str) -> list[str]:
        dt = pd.to_datetime(date_str)
        if dt not in df_sue_ffill.index:
            return []
        scores = df_sue_ffill.loc[dt].dropna()
        if len(scores) < 50:
            return []
        # Top quintile (Q5)
        try:
            qcut = pd.qcut(scores.rank(method='first'), 5, labels=[1,2,3,4,5])
            top_symbols = qcut[qcut == 5].index.tolist()
            return top_symbols
        except ValueError:
            return []

    # ---------------------------------------------------------------------
    # 2. Momentum Signal Generator
    # ---------------------------------------------------------------------
    log.info("Preparing Momentum signals (6M)...")
    # For speed, we will calculate 6M return (126 trading days) using a pivot table
    df_price_pivot = df_price.pivot(index='date', columns='symbol', values='close')
    df_price_pivot = df_price_pivot.reindex(all_dates_idx)
    
    lookback_days = 126 # ~6 months
    df_mom = df_price_pivot.pct_change(periods=lookback_days)
    
    def momentum_signal_func(date_str: str) -> list[str]:
        # We will test D1 (Reversal / Losers) as it had positive edge previously
        dt = pd.to_datetime(date_str)
        if dt not in df_mom.index:
            return []
        scores = df_mom.loc[dt].dropna()
        if len(scores) < 50:
            return []
        try:
            qcut = pd.qcut(scores.rank(method='first'), 10, labels=range(1, 11))
            # Return D1 (Bottom decile)
            top_symbols = qcut[qcut == 1].index.tolist()
            return top_symbols
        except ValueError:
            return []

    # ---------------------------------------------------------------------
    # 3. Run Simulations
    # ---------------------------------------------------------------------
    combinations = [
        ("SUE (Top 20%)", sue_signal_func),
        ("6M Mom (Bottom 10%)", momentum_signal_func)
    ]
    rebalance_freqs = [5, 10, 20]
    
    results_summary = []
    
    for sig_name, sig_func in combinations:
        for rb_days in rebalance_freqs:
            log.info(f"Running simulation: {sig_name} | Rebalance: {rb_days} days")
            port_df = simulate_portfolio(trading_dates, price_series_by_sym, sig_func, rebalance_days=rb_days)
            
            if port_df.empty:
                continue
                
            port_df.set_index('date', inplace=True)
            
            # Align with benchmark
            aligned = port_df.join(bench_by_date[['daily_ret', 'cum_ret']], rsuffix='_bench').dropna()
            
            # Overall metrics
            total_ret = aligned['cum_ret'].iloc[-1]
            bench_ret = aligned['cum_ret_bench'].iloc[-1]
            
            mdd = calculate_mdd(aligned['cum_ret'])
            bench_mdd = calculate_mdd(aligned['cum_ret_bench'])
            
            vol = calculate_volatility(aligned['daily_ret'])
            bench_vol = calculate_volatility(aligned['daily_ret_bench'])
            
            # Sub-period analysis (Robustness)
            periods = {
                'Full (2021-2026)': aligned,
                'A (2021-2022)': aligned['2021':'2022'],
                'B (2023-2026)': aligned['2023':'2026'],
                'Split2_A (2021)': aligned['2021':'2021'],
                'Split2_B (2022-2026)': aligned['2022':'2026'],
                'Split3_A (2023-2024)': aligned['2023':'2024'],
                'Split3_B (2025-2026)': aligned['2025':'2026'],
                'Split4_A (2021-2024)': aligned['2021':'2024']
            }
            
            res_dict = {
                'Signal': sig_name,
                'Rebal_Days': rb_days,
                'Full_Ret': total_ret,
                'Full_Bench': bench_ret,
                'Full_MDD': mdd,
                'Full_Vol': vol
            }
            
            for p_name, p_df in periods.items():
                if p_df.empty:
                    continue
                # Calculate period specific return
                # (1 + r_end) / (1 + r_start) - 1
                start_val = 1 + p_df['cum_ret'].iloc[0]
                end_val = 1 + p_df['cum_ret'].iloc[-1]
                p_ret = end_val / start_val - 1
                
                b_start = 1 + p_df['cum_ret_bench'].iloc[0]
                b_end = 1 + p_df['cum_ret_bench'].iloc[-1]
                b_ret = b_end / b_start - 1
                
                res_dict[f"{p_name}_Ret"] = p_ret
                res_dict[f"{p_name}_Bench"] = b_ret
                
            results_summary.append(res_dict)

    print("\n" + "="*80)
    print("포트폴리오 시계열 시뮬레이션 결과 요약 (동일가중, 전체 재구성)")
    print("="*80)
    
    for r in results_summary:
        print(f"\n[{r['Signal']}] 리밸런싱: {r['Rebal_Days']}일")
        print(f"  전체 기간 (2021-2026): 수익률 {r['Full_Ret']*100:>6.2f}% (벤치마크 {r['Full_Bench']*100:>6.2f}%) | MDD {r['Full_MDD']*100:>6.2f}% (벤치마크 {bench_mdd*100:>6.2f}%)")
        print(f"  기간 A (2021-2022)  : 수익률 {r['A (2021-2022)_Ret']*100:>6.2f}% (벤치마크 {r['A (2021-2022)_Bench']*100:>6.2f}%)")
        print(f"  기간 B (2023-2026)  : 수익률 {r['B (2023-2026)_Ret']*100:>6.2f}% (벤치마크 {r['B (2023-2026)_Bench']*100:>6.2f}%)")
        print(f"  [로버스트니스] 2021년 : 수익률 {r.get('Split2_A (2021)_Ret', 0)*100:>6.2f}% (벤치마크 {r.get('Split2_A (2021)_Bench', 0)*100:>6.2f}%)")
        print(f"  [로버스트니스] 2021-24: 수익률 {r.get('Split4_A (2021-2024)_Ret', 0)*100:>6.2f}% (벤치마크 {r.get('Split4_A (2021-2024)_Bench', 0)*100:>6.2f}%)")
        print(f"  [로버스트니스] 2022-26: 수익률 {r.get('Split2_B (2022-2026)_Ret', 0)*100:>6.2f}% (벤치마크 {r.get('Split2_B (2022-2026)_Bench', 0)*100:>6.2f}%)")
        print(f"  [로버스트니스] 2023-24: 수익률 {r.get('Split3_A (2023-2024)_Ret', 0)*100:>6.2f}% (벤치마크 {r.get('Split3_A (2023-2024)_Bench', 0)*100:>6.2f}%)")
        print(f"  [로버스트니스] 2025-26: 수익률 {r.get('Split3_B (2025-2026)_Ret', 0)*100:>6.2f}% (벤치마크 {r.get('Split3_B (2025-2026)_Bench', 0)*100:>6.2f}%)")
        
if __name__ == "__main__":
    main()
