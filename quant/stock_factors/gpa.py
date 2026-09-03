import sqlite3
import pandas as pd
from pathlib import Path
from quant.stock_factors.config import FactorConfig

DB_PATH = Path(__file__).parent.parent.parent / "data" / "quant.db"

def prepare_gpa_signals(start_date: str, config: FactorConfig) -> dict:
    """
    GPA (Gross Profit to Assets) 팩터 신호 생성
    - GPA = 매출총이익 / 자산총계
    - lookback_quarters 분기 롤링 평균 적용
    - Point-in-time 원칙 준수 (rcept_dt 기준)
    """
    conn = sqlite3.connect(DB_PATH)
    
    query = """
        SELECT symbol, rcept_dt as date, report_code, metric, quarterly_value
        FROM pead_quarterly_normalized
        WHERE metric IN ('gross_profit', 'total_assets')
        ORDER BY date
    """
    df = pd.read_sql(query, conn)
    conn.close()
    
    # 분기별 스냅샷 생성
    df_pivot = df.pivot_table(
        index=['symbol', 'date', 'report_code'], 
        columns='metric', 
        values='quarterly_value'
    ).reset_index()
    
    # 데이터 부족 시 제외
    df_pivot = df_pivot.dropna(subset=['gross_profit', 'total_assets'])
    
    # 동일 날짜에 2개 이상의 보고서(report_code)가 접수된 경우 최신 보고서 하나만 유지
    df_pivot = df_pivot.sort_values(['symbol', 'date', 'report_code']).drop_duplicates(subset=['symbol', 'date'], keep='last')
    
    df_pivot['gpa_raw'] = df_pivot['gross_profit'] / df_pivot['total_assets']
    
    # 종목별 롤링 평균 적용
    df_pivot = df_pivot.sort_values(['symbol', 'date'])
    df_pivot['gpa_rolling'] = df_pivot.groupby('symbol')['gpa_raw'].transform(
        lambda x: x.rolling(config.lookback_quarters, min_periods=config.min_periods).mean()
    )
    df_pivot = df_pivot.dropna(subset=['gpa_rolling'])
    
    # 날짜별로 조회 가능한 형태 (dict)로 변환
    df_pivot['date'] = pd.to_datetime(df_pivot['date'])
    
    # 일별 조회를 위해 ffill 적용 
    # (실제 백테스터에서는 특정 날짜에 가용한 최신값을 찾음)
    df_gpa = df_pivot.pivot(index='date', columns='symbol', values='gpa_rolling')
    
    # 모든 거래일에 대해 ffill
    query_dates = f"SELECT DISTINCT date FROM pead_price_raw WHERE date >= '{start_date}'"
    conn = sqlite3.connect(DB_PATH)
    trading_dates = pd.read_sql(query_dates, conn)['date']
    conn.close()
    
    all_dates_idx = pd.DatetimeIndex(trading_dates)
    df_gpa = df_gpa.reindex(all_dates_idx.union(df_gpa.index)).sort_index()
    df_gpa_ffill = df_gpa.ffill().reindex(all_dates_idx)
    
    return df_gpa_ffill

def get_gpa_signal_func(df_gpa_ffill: pd.DataFrame, top_percentile: float = 0.2):
    """
    주어진 날짜에 GPA 팩터 상위 N% 종목을 반환하는 함수 (팩터 방향: 클수록 좋음)
    """
    def signal_func(date_str: str) -> list:
        dt = pd.to_datetime(date_str)
        if dt not in df_gpa_ffill.index: return []
        scores = df_gpa_ffill.loc[dt].dropna()
        if len(scores) < 50: return []
        
        # GPA 상위 N% 매수
        # rank(ascending=False) : 값이 클수록 순위가 높음 (1등)
        num_target = int(len(scores) * top_percentile)
        return scores.nlargest(num_target).index.tolist()
        
    return signal_func
