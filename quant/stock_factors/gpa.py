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
    
    # basis='CFS' 고정 - value.py::prepare_pbr_signals와 동일한 이유
    # (CFS/OFS 미필터링 시 pivot_table의 mean aggfunc가 total_assets를 오염시킴).
    query = """
        SELECT symbol, target_year, rcept_dt as date, report_code, metric, quarterly_value
        FROM pead_quarterly_normalized
        WHERE metric IN ('gross_profit', 'total_assets') AND basis = 'CFS'
        ORDER BY date
    """
    df = pd.read_sql(query, conn)
    conn.close()
    
    # 분기별 스냅샷 생성
    df_pivot = df.pivot_table(
        index=['symbol', 'date', 'target_year', 'report_code'],
        columns='metric', 
        values='quarterly_value'
    ).reset_index()
    
    # 데이터 부족 시 제외
    df_pivot = df_pivot.dropna(subset=['gross_profit', 'total_assets'])
    
    df_pivot['gpa_raw'] = df_pivot['gross_profit'] / df_pivot['total_assets']
    quarters = df_pivot['report_code'].map({"11013": 1, "11012": 2, "11014": 3, "11011": 4})
    df_pivot['period'] = df_pivot['target_year'].astype(int) * 4 + quarters
    df_pivot = df_pivot.sort_values(['symbol', 'date', 'period'])

    # Rolling mean over the latest lookback_quarters *quarters* known on each
    # filing date. An amendment replaces its quarter's value rather than
    # entering the window as an extra quarter (value.py has the same rule).
    rolled = []
    for _, filings in df_pivot.groupby('symbol', sort=False):
        known = {}
        for date, day in filings.groupby('date', sort=False):
            known.update(zip(day['period'], day['gpa_raw'], strict=True))
            window = [known[p] for p in sorted(known)[-config.lookback_quarters:]]
            mean = sum(window) / len(window) if len(window) >= config.min_periods else None
            rolled.append((day['symbol'].iloc[0], date, mean))
    df_pivot = pd.DataFrame(rolled, columns=['symbol', 'date', 'gpa_rolling'])
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

    TODO: value.py::get_pbr_signal_func는 무상감자/유상증자 등으로 발행주식수가 급변한
    직후~다음 분기보고서 확정 전 구간을 격리(quarantine)해 후보에서 제외한다
    (RESEARCH_LOG.md 섹션 15/16). GPA = gross_profit/total_assets는 주당가격이나
    발행주식수를 쓰지 않아 같은 경로로 직접 오염되진 않지만, total_assets 자체가 급변하는
    이벤트(합병/분할 등)에 유사하게 노출될 가능성은 검토되지 않았음 - 이번 스코프에서 제외.
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
