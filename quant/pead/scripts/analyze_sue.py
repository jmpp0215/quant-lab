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
from quant.pead.storage import save_surprise
from quant.pead.benchmark import calculate_excess_return

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

    # 2b. Load benchmark (KOSPI) index data
    df_bench = pd.read_sql("SELECT date, close FROM pead_benchmark_raw WHERE index_symbol = 'KS11'", conn)
    if df_bench.empty:
        log.error("No benchmark data found. Please run backfill_benchmark.py first.")
        return
    # benchmark.calculate_excess_return이 요구하는 {YYYYMMDD 문자열: 종가} 형태의 Series로 변환
    benchmark_price_series = pd.Series(df_bench['close'].values, index=df_bench['date'])

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

        # is_estimable=False인 이벤트도 그대로 저장한다 (surprise_score=NULL) — 나중에
        # 계산 불가 비율을 파악하려면 "계산을 시도했지만 데이터가 부족했다"는 기록 자체가 필요하다.
        save_surprise(sym, rcept_dt_str, config, sue, is_estimable)

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

        # 종목 수익률과 동일한 기준(entry_timing)으로 벤치마크(KOSPI) 대비 초과수익 계산
        excess_ret_20d = calculate_excess_return(
            sym, rcept_dt_str, ret_20d, benchmark_price_series, config
        )

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
            'excess_ret_20d': excess_ret_20d,
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
    
    # Calculate Mean, Median, and Count for both raw and benchmark-excess returns
    summary = top_sue.groupby('adtv_quintile').agg(
        count=('ret_20d', 'count'),
        mean_raw=('ret_20d', 'mean'),
        median_raw=('ret_20d', 'median'),
        mean_excess=('excess_ret_20d', 'mean'),
        median_excess=('excess_ret_20d', 'median'),
    ).reset_index()

    print("\n=== [분석 결과] SUE 상위 Decile (Top 10%) 종목의 거래대금 Quintile별 20일 수익률 (절대 vs KOSPI 대비 초과) ===")
    print(f"{'유동성 Quintile':<15} | {'N (표본수)':<10} | {'평균(절대,%)':<12} | {'중앙값(절대,%)':<14} | {'평균(초과,%)':<12} | {'중앙값(초과,%)':<14}")
    print("-" * 95)
    for _, row in summary.iterrows():
        q = int(row['adtv_quintile'])
        count = int(row['count'])
        if count < 30:
            print(f"Q{q:<14} | {count:<10} | 표본 부족     | 표본 부족       | 표본 부족     | 표본 부족")
        else:
            print(
                f"Q{q:<14} | {count:<10} | {row['mean_raw']*100:>10.2f}% | {row['median_raw']*100:>12.2f}% "
                f"| {row['mean_excess']*100:>10.2f}% | {row['median_excess']*100:>12.2f}%"
            )

    print("\n(참고: Q1 = 거래대금 하위 20%, Q5 = 거래대금 상위 20%. 초과수익 = 종목 20일 수익률 - 같은 기간 KOSPI 20일 수익률)")
    print(f"분석 대상 이벤트 수(Top SUE): {len(top_sue)}건")
    
    # 6. Spearman Rank Correlation (using ranked pearson to avoid scipy dependency)
    corr = df_res['sue'].rank().corr(df_res['ret_20d'].rank())
    print("\n=== [상관 분석] 전체 표본 대상 SUE와 20일 수익률의 Spearman 순위 상관계수 ===")
    print(f"전체 유효 이벤트 수: {len(df_res)}건")
    print(f"Spearman Correlation: {corr:.4f}")
    
if __name__ == "__main__":
    main()
