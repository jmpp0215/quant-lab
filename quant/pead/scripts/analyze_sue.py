import os
import sys
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
import logging

sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from quant.pead.config import PeadConfig
from quant.pead.signal import build_event_timeline, calculate_surprise

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("analyze_sue")

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "quant.db"

def main():
    if not DB_PATH.exists():
        log.error("Database not found. Please run backfill_dart.py and backfill_prices.py first.")
        return
        
    conn = sqlite3.connect(DB_PATH)
    
    # 1. Load normalized data
    df_fin = pd.read_sql("SELECT * FROM pead_quarterly_normalized WHERE is_estimable=1", conn)
    if df_fin.empty:
        log.error("No normalized financial data found.")
        return
        
    # 2. Load price data
    df_price = pd.read_sql("SELECT date, symbol, close, trading_value FROM pead_price_raw", conn)
    if df_price.empty:
        log.error("No price data found.")
        return
        
    df_price['date'] = pd.to_datetime(df_price['date'])
    df_price = df_price.sort_values('date')
    
    config = PeadConfig(lookback_quarters=4, earnings_metric="operating_income", dart_basis="CFS")
    
    # 3. Calculate SUE and Forward Returns
    results = []
    
    # Get unique reporting dates per symbol
    events = df_fin[['symbol', 'rcept_dt']].drop_duplicates()
    
    log.info(f"Processing {len(events)} events for SUE and returns...")
    
    # To optimize price lookups
    price_by_sym = dict(tuple(df_price.groupby('symbol')))
    # build_event_timeline이 요구하는 {YYYYMMDD: close} 형태로 종목별 가격 시리즈를 미리 변환
    price_series_by_sym = {
        sym: dict(zip(p_df['date'].dt.strftime('%Y%m%d'), p_df['close'], strict=True))
        for sym, p_df in price_by_sym.items()
    }

    for _, row in events.iterrows():
        sym = row['symbol']
        rcept_dt_str = row['rcept_dt']

        # Calculate SUE (config.earnings_metric/dart_basis에 맞는 데이터만 내부에서 필터링됨)
        sym_records = df_fin[df_fin['symbol'] == sym].to_dict('records')
        sue, is_estimable = calculate_surprise(sym, rcept_dt_str, config, sym_records)

        if not is_estimable:
            continue

        # Get price series for the symbol
        if sym not in price_series_by_sym:
            continue

        # config.entry_timing(t+1/t+2)을 반영한 진입가/누적수익률 계산은 signal.py가 전담
        timeline = build_event_timeline(sym, rcept_dt_str, price_series_by_sym[sym], config)
        if timeline is None or timeline['return_20d'] is None:
            continue

        ret_20d = timeline['return_20d']

        # Calculate ADTV (Average Daily Traded Value) past 20 days
        p_df = price_by_sym[sym]
        event_dt = pd.to_datetime(rcept_dt_str)
        pre_event = p_df[p_df['date'] < event_dt]
        if len(pre_event) < 20:
            adtv = pre_event['trading_value'].mean()
        else:
            adtv = pre_event.tail(20)['trading_value'].mean()

        results.append({
            'symbol': sym,
            'rcept_dt': rcept_dt_str,
            'sue': sue,
            'ret_20d': ret_20d,
            'adtv': adtv
        })
        
    df_res = pd.DataFrame(results)
    if df_res.empty:
        log.error("No valid events with complete data.")
        return
        
    log.info(f"Computed metrics for {len(df_res)} events.")
    
    # 4. Grouping by Quintile and Decile
    # Cross-sectional grouping (normally done period-by-period, but for simple analysis we can do overall or yearly)
    # The prompt asks for simple quintile grouping
    
    # Create ADTV quintiles (1=Lowest Liquidity, 5=Highest)
    df_res['adtv_quintile'] = pd.qcut(df_res['adtv'].rank(method='first'), 5, labels=[1, 2, 3, 4, 5])
    
    # Create SUE deciles (1=Lowest SUE, 10=Highest SUE)
    df_res['sue_decile'] = pd.qcut(df_res['sue'].rank(method='first'), 10, labels=range(1, 11))
    
    # 5. Analysis: Average and Median 20-day return for SUE Top Decile by ADTV Quintile
    top_sue = df_res[df_res['sue_decile'] == 10]
    
    # Calculate Mean, Median, and Count
    summary = top_sue.groupby('adtv_quintile')['ret_20d'].agg(['mean', 'median', 'count']).reset_index()
    summary['mean_pct'] = summary['mean'] * 100
    summary['median_pct'] = summary['median'] * 100
    
    print("\n=== [분석 결과] SUE 상위 Decile (Top 10%) 종목의 거래대금 Quintile별 20일 수익률 ===")
    print(f"{'유동성 Quintile':<15} | {'N (표본수)':<10} | {'평균 수익률(%)':<15} | {'중앙값(Median)(%)':<15}")
    print("-" * 65)
    for _, row in summary.iterrows():
        q = int(row['adtv_quintile'])
        count = int(row['count'])
        if count < 30:
            print(f"Q{q:<14} | {count:<10} | 표본 부족        | 표본 부족")
        else:
            print(f"Q{q:<14} | {count:<10} | {row['mean_pct']:>11.2f}% | {row['median_pct']:>13.2f}%")
        
    print("\n(참고: Q1 = 거래대금 하위 20%, Q5 = 거래대금 상위 20%)")
    print(f"분석 대상 이벤트 수(Top SUE): {len(top_sue)}건")
    
    # 6. Spearman Rank Correlation (using ranked pearson to avoid scipy dependency)
    corr = df_res['sue'].rank().corr(df_res['ret_20d'].rank())
    print("\n=== [상관 분석] 전체 표본 대상 SUE와 20일 수익률의 Spearman 순위 상관계수 ===")
    print(f"전체 유효 이벤트 수: {len(df_res)}건")
    print(f"Spearman Correlation: {corr:.4f}")
    
if __name__ == "__main__":
    main()
