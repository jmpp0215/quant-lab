import sys
import sqlite3
import pandas as pd
from pathlib import Path
import logging

sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from quant.stock_factors.config import FactorConfig
from quant.stock_factors.signal import calculate_momentum_score, cross_sectional_ranking
from quant.stock_factors.storage import init_db, save_momentum_scores

from quant.pead.signal import build_event_timeline
from quant.pead.benchmark import calculate_excess_return

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("analyze_xmom")

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "quant.db"

def main():
    if not DB_PATH.exists():
        log.error("Database not found.")
        return
        
    init_db()
    conn = sqlite3.connect(DB_PATH)
    
    # 1. Load price data
    log.info("Loading price data...")
    df_price = pd.read_sql("SELECT date, symbol, close, trading_value FROM pead_price_raw", conn)
    if df_price.empty:
        log.error("No price data found.")
        return
        
    df_price['date'] = pd.to_datetime(df_price['date'])
    df_price = df_price.sort_values('date')
    
    # 2. Load benchmark
    log.info("Loading benchmark data...")
    df_bench = pd.read_sql("SELECT date, close FROM pead_benchmark_raw WHERE index_symbol = 'KS11'", conn)
    if df_bench.empty:
        log.error("No benchmark data found.")
        return
    benchmark_price_series = pd.Series(df_bench['close'].values, index=df_bench['date'])

    # Configs to test
    lookbacks = [3, 6, 12]
    
    price_by_sym = dict(tuple(df_price.groupby('symbol')))
    price_series_by_sym = {
        sym: dict(zip(p_df['date'].dt.strftime('%Y%m%d'), p_df['close'], strict=True))
        for sym, p_df in price_by_sym.items()
    }
    
    # Determine end of month rebalance dates
    # Assuming rebalancing at the last trading day of each month
    all_dates = pd.Series(df_price['date'].unique()).sort_values()
    # groupby year and month and take max
    eom_dates = all_dates.groupby([all_dates.dt.year, all_dates.dt.month]).max()
    rebalance_dates = eom_dates.dt.strftime('%Y%m%d').tolist()
    
    # Exclude the first 12 months for warm-up to ensure we have enough data for 12m momentum
    warmup_idx = 12
    if len(rebalance_dates) > warmup_idx:
        rebalance_dates = rebalance_dates[warmup_idx:]
    else:
        log.warning("Not enough data for 12-month warmup. Using available dates.")
        
    for lookback in lookbacks:
        config = FactorConfig(lookback_months=lookback, entry_timing="t+1")
        log.info(f"=== Running analysis for {lookback} Month Momentum ===")
        
        all_results = []
        
        for r_date in rebalance_dates:
            scores = {}
            valid_symbols = []
            
            # Step A: Calculate score for all symbols at r_date
            for sym, p_series in price_series_by_sym.items():
                score, is_est = calculate_momentum_score(sym, r_date, p_series, config)
                
                # We need some volume/liquidity check, but for now we just use is_est
                if is_est and score is not None:
                    # check liquidity (ADTV 20)
                    p_df = price_by_sym[sym]
                    pre_event = p_df[p_df['date'] <= pd.to_datetime(r_date)]
                    if len(pre_event) >= 20:
                        adtv = pre_event.tail(20)['trading_value'].mean()
                        # Minimum liquidity filter (e.g. 1 billion KRW) - Optional but good practice
                        # We will include ADTV so we can filter/view it later
                        scores[sym] = score
                        valid_symbols.append((sym, adtv))
            
            if not scores:
                continue
                
            # Step B: Cross-sectional ranking (deciles)
            ranks = cross_sectional_ranking(scores, quantiles=10)
            
            # Step C: Save to DB and calculate forward returns
            db_records = []
            for sym, adtv in valid_symbols:
                score = scores[sym]
                rank = ranks.get(sym)
                
                db_records.append({
                    'symbol': sym,
                    'score': score,
                    'rank': rank,
                    'is_estimable': True
                })
                
                if rank is None:
                    continue
                    
                # Calculate Forward 20d Returns using build_event_timeline (PEAD code)
                timeline = build_event_timeline(sym, r_date, price_series_by_sym[sym], config)
                if timeline is None or timeline['return_20d'] is None:
                    continue
                    
                ret_20d = timeline['return_20d']
                excess_ret_20d = calculate_excess_return(sym, r_date, ret_20d, benchmark_price_series, config)
                
                all_results.append({
                    'rebalance_date': r_date,
                    'symbol': sym,
                    'score': score,
                    'decile': rank,
                    'ret_20d': ret_20d,
                    'excess_ret_20d': excess_ret_20d,
                    'adtv': adtv
                })
                
            save_momentum_scores(r_date, config, db_records)
            
        # Analysis for this lookback
        df_res = pd.DataFrame(all_results)
        if df_res.empty:
            log.warning(f"No valid results for {lookback}m momentum.")
            continue
            
        print(f"\n[{lookback}개월 모멘텀] 상위 Decile (D10) 매수 시 20일 수익률 검증")
        
        # 1. Event Level (All rebalance instances)
        top_decile = df_res[df_res['decile'] == 10]
        n_events = len(top_decile)
        mean_raw = top_decile['ret_20d'].mean() * 100
        median_raw = top_decile['ret_20d'].median() * 100
        mean_exc = top_decile['excess_ret_20d'].mean() * 100
        median_exc = top_decile['excess_ret_20d'].median() * 100
        
        print("\n[이벤트 레벨 (리밸런싱 횟수 x 종목수)]")
        print(f"N: {n_events}")
        print(f"평균(절대): {mean_raw:.2f}% | 중앙값(절대): {median_raw:.2f}%")
        print(f"평균(초과): {mean_exc:.2f}% | 중앙값(초과): {median_exc:.2f}%")
        
        # 2. Symbol Level (Aggregate by symbol to check for outlier reliance)
        sym_agg = top_decile.groupby('symbol').agg(
            n_times=('rebalance_date', 'count'),
            mean_exc=('excess_ret_20d', 'mean'),
            median_exc=('excess_ret_20d', 'median')
        ).reset_index()
        
        n_syms = len(sym_agg)
        sym_mean_exc = sym_agg['mean_exc'].mean() * 100
        sym_median_exc = sym_agg['mean_exc'].median() * 100
        
        print("\n[종목 레벨 (종목별 평균의 평균)]")
        print(f"등장한 고유 종목수: {n_syms}")
        print(f"초과수익 종목단위 평균: {sym_mean_exc:.2f}% | 초과수익 종목단위 중앙값: {sym_median_exc:.2f}%")
        
        # 3. Decile Spread Analysis (Q10 vs Q1)
        bottom_decile = df_res[df_res['decile'] == 1]
        mean_exc_q1 = bottom_decile['excess_ret_20d'].mean() * 100
        print(f"\n(참고) 하위 Decile (D1) 이벤트 레벨 평균 초과수익: {mean_exc_q1:.2f}%")
        print(f"Long-Short Spread (D10 - D1): {mean_exc - mean_exc_q1:.2f}%")
        print("-" * 60)

if __name__ == "__main__":
    main()
