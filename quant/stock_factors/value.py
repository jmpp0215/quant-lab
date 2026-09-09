import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "quant.db"

def prepare_pbr_signals(start_date: str) -> pd.DataFrame:
    """
    PBR (Price to Book Ratio) 팩터 신호 생성을 위한 BPS(주당순자산) 데이터 준비
    - BPS = 자본총계 / 발행주식수
    - Point-in-time 원칙 준수 (rcept_dt 기준)
    - PBR은 일간 주가에 따라 매일 변동하므로, 분기별 BPS만 ffill하여 반환
    """
    conn = sqlite3.connect(DB_PATH)
    
    query = """
        SELECT symbol, rcept_dt as date, report_code, metric, quarterly_value
        FROM pead_quarterly_normalized
        WHERE metric IN ('total_equity', 'issued_shares')
        ORDER BY date
    """
    df = pd.read_sql(query, conn)
    conn.close()
    
    df_pivot = df.pivot_table(
        index=['symbol', 'date', 'report_code'], 
        columns='metric', 
        values='quarterly_value'
    ).reset_index()
    
    df_pivot = df_pivot.dropna(subset=['total_equity', 'issued_shares'])
    
    # 동일 날짜에 2개 이상의 보고서가 접수된 경우 방지
    df_pivot = df_pivot.sort_values(['symbol', 'date', 'report_code']).drop_duplicates(subset=['symbol', 'date'], keep='last')
    
    # 주식수가 0인 경우 방지
    df_pivot = df_pivot[df_pivot['issued_shares'] > 0]
    df_pivot['bps'] = df_pivot['total_equity'] / df_pivot['issued_shares']
    
    df_pivot['date'] = pd.to_datetime(df_pivot['date'])
    df_bps = df_pivot.pivot(index='date', columns='symbol', values='bps')
    
    query_dates = f"SELECT DISTINCT date FROM pead_price_raw WHERE date >= '{start_date}'"
    conn = sqlite3.connect(DB_PATH)
    trading_dates = pd.read_sql(query_dates, conn)['date']
    conn.close()
    
    all_dates_idx = pd.DatetimeIndex(trading_dates)
    df_bps = df_bps.reindex(all_dates_idx.union(df_bps.index)).sort_index()
    df_bps_ffill = df_bps.ffill().reindex(all_dates_idx)
    
    return df_bps_ffill

def get_pbr_signal_func(df_bps_ffill: pd.DataFrame, df_price_pivot: pd.DataFrame, top_percentile: float = 0.2):
    """
    주어진 날짜에 PBR 팩터 하위 N% (저평가) 종목을 반환하는 함수
    - PBR = Price / BPS
    - 팩터 방향: 작을수록 좋음 (가치주)
    """
    def signal_func(date_str: str) -> list:
        dt = pd.to_datetime(date_str)
        if dt not in df_bps_ffill.index or dt not in df_price_pivot.index: 
            return []
            
        bps = df_bps_ffill.loc[dt]
        price = df_price_pivot.loc[dt]
        
        # BPS가 양수인 종목만 대상 (자본잠식 제외)
        valid = (bps > 0) & price.notna()
        bps = bps[valid]
        price = price[valid]
        
        if len(bps) < 50: return []
        
        # align indexes
        common_idx = bps.index.intersection(price.index)
        pbr = price[common_idx] / bps[common_idx]
        
        # PBR 하위 N% 매수 (값이 작을수록 저평가)
        num_target = int(len(pbr) * top_percentile)
        return pbr.nsmallest(num_target).index.tolist()
        
    return signal_func
