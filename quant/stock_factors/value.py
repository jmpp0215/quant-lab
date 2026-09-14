import logging
import sqlite3
from pathlib import Path

import pandas as pd

from quant.common.price_guard import DEFAULT_ANOMALY_THRESHOLD, find_anomalies

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "data" / "quant.db"

# 이벤트 발생 후 확정 분기보고서(issued_shares)가 이 기간(일) 안에 오지 않으면 "장기 미확정"으로
# 취급 - 격리 자체는 확정 전까지 계속 유지되므로(안전한 기본값) 매수 후보 자격에는 영향이 없지만,
# 이 상태는 로그로 별도 escalate해 사람이 확인하도록 한다. RESEARCH_LOG.md 섹션 15/16 참고:
# 실제 확인된 이상치->확정 보고서 간격은 4~106일이었고, 전체 종목의 정상 분기 공시 간격도
# p90=154일/p99=273일이므로 180일은 정상 케이스를 거의 포괄하면서 비정상적으로 긴 케이스만
# 골라내는 값이다.
QUARANTINE_ESCALATION_DAYS = 180

def prepare_pbr_signals(start_date: str) -> pd.DataFrame:
    """
    PBR (Price to Book Ratio) 팩터 신호 생성을 위한 BPS(주당순자산) 데이터 준비
    - BPS = 자본총계 / 발행주식수
    - Point-in-time 원칙 준수 (rcept_dt 기준)
    - PBR은 일간 주가에 따라 매일 변동하므로, 분기별 BPS만 ffill하여 반환
    """
    conn = sqlite3.connect(DB_PATH)
    
    # basis='CFS'로 고정: DART는 연결(CFS)/별도(OFS) 재무제표를 모두 반환하고
    # pead_quarterly_normalized에 둘 다 별도 행으로 저장된다 (quant/pead/dart.py).
    # basis를 필터링하지 않으면 이후 pivot_table의 기본 aggfunc='mean'이 두 값을
    # 조용히 평균 내버려 자본총계가 오염된다 - quant/pead/signal.py의
    # _get_point_in_time_series, quant/pead/config.py의 PeadConfig.dart_basis와
    # 동일한 관례를 따른다. issued_shares는 dart.py에서 항상 basis='CFS'로만
    # 저장되므로 이 필터는 그쪽 동작을 바꾸지 않는다.
    query = """
        SELECT symbol, rcept_dt as date, report_code, metric, quarterly_value
        FROM pead_quarterly_normalized
        WHERE metric IN ('total_equity', 'issued_shares') AND basis = 'CFS'
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


def load_issued_shares_rcept_dts() -> dict[str, list[str]]:
    """종목별 issued_shares(발행주식수, basis='CFS') 분기보고서 접수일(rcept_dt) 목록.

    compute_quarantine_windows()가 가격 이상치 이후 "확정 시점"을 찾는 데 쓴다.
    """
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        "SELECT DISTINCT symbol, rcept_dt FROM pead_quarterly_normalized "
        "WHERE metric = 'issued_shares' AND basis = 'CFS'",
        conn,
    )
    conn.close()
    result: dict[str, list[str]] = {}
    for symbol, group in df.groupby("symbol")["rcept_dt"]:
        result[symbol] = sorted(group.tolist())
    return result


def _next_confirming_rcept_dt(event_date: str, rcept_dts: list[str]) -> str | None:
    return next((r for r in rcept_dts if r >= event_date), None)


def compute_quarantine_windows(
    price_series_by_sym: dict[str, dict[str, float]],
    issued_shares_rcept_by_sym: dict[str, list[str]],
    threshold: float = DEFAULT_ANOMALY_THRESHOLD,
    escalation_days: int = QUARANTINE_ESCALATION_DAYS,
) -> dict[str, list[tuple[str, str | None]]]:
    """종목별 "매수 후보 자격 정지" 구간을 계산.

    가격 이상치(quant.common.price_guard.find_anomalies)를 발행주식수 급변(무상감자/
    유상증자/액면병합 등) 이벤트의 대리 신호로 쓴다. 이벤트 발생일부터, 그 이후 최초로
    접수된 issued_shares 분기보고서(rcept_dt)까지를 격리 구간으로 잡는다 - 그 사이엔
    BPS가 오래된 발행주식수와 새 가격을 섞어 계산되어 PBR이 실제와 다르게 산출될 수 있다.

    같은 종목에서 격리가 풀리기 전에(즉 확정 보고서가 오기 전에) 또 다른 이상치가 발생하면
    두 구간을 이어붙여 하나로 연장한다 (체인).

    확정 보고서가 없는 채로 escalation_days가 지나도 격리 자체는 계속 유지된다(자격을
    다시 주기엔 안전하지 않으므로) - 다만 이 상태는 별도로 로그 경고를 남겨, "이상치 이후
    장기간 공시가 없다"는 그 자체가 관리종목류 위험 신호일 수 있음을 사람이 확인하도록 한다
    (RESEARCH_LOG.md 섹션 14의 관리종목 전부 제외 방침과 같은 철학).

    Returns:
        {symbol: [(start_date, end_date_or_None), ...]} - end가 None이면 아직 확정 보고서가
        도착하지 않아 무기한 격리 중이라는 뜻 (안전한 기본값).
    """
    windows_by_symbol: dict[str, list[tuple[str, str | None]]] = {}

    for symbol, price_series in price_series_by_sym.items():
        anomalies = sorted(a.date for a in find_anomalies(symbol, price_series, threshold))
        if not anomalies:
            continue

        rcept_dts = issued_shares_rcept_by_sym.get(symbol, [])

        merged: list[tuple[str, str | None]] = []
        cur_start: str | None = None
        cur_end: str | None = None
        for event_date in anomalies:
            still_open = cur_start is not None and (cur_end is None or event_date <= cur_end)
            if still_open:
                cur_end = _next_confirming_rcept_dt(event_date, rcept_dts)
            else:
                if cur_start is not None:
                    merged.append((cur_start, cur_end))
                cur_start = event_date
                cur_end = _next_confirming_rcept_dt(event_date, rcept_dts)
        if cur_start is not None:
            merged.append((cur_start, cur_end))

        for start, end in merged:
            if end is None:
                log.warning(
                    "%s: price anomaly on %s has no confirming issued_shares filing yet - "
                    "quarantined indefinitely until one arrives",
                    symbol, start,
                )
            else:
                gap_days = (pd.to_datetime(end) - pd.to_datetime(start)).days
                if gap_days > escalation_days:
                    log.warning(
                        "%s: price anomaly on %s took %d days to confirm (rcept_dt %s), "
                        "exceeding the %d-day escalation threshold - investigate manually, "
                        "this pattern correlates with 관리종목-type risk",
                        symbol, start, gap_days, end, escalation_days,
                    )

        windows_by_symbol[symbol] = merged

    return windows_by_symbol


def get_pbr_signal_func(
    df_bps_ffill: pd.DataFrame,
    df_price_pivot: pd.DataFrame,
    top_percentile: float = 0.2,
    quarantine: dict[str, list[tuple[str, str | None]]] | None = None,
):
    """
    주어진 날짜에 PBR 팩터 하위 N% (저평가) 종목을 반환하는 함수
    - PBR = Price / BPS
    - 팩터 방향: 작을수록 좋음 (가치주)
    - quarantine: compute_quarantine_windows()의 결과. 해당 날짜가 종목 자신의 격리 구간에
      들어있으면 랭킹 전에 후보군에서 제외한다.
    """
    quarantine = quarantine or {}

    def _is_quarantined(symbol: str, date_str: str) -> bool:
        for start, end in quarantine.get(symbol, []):
            if start <= date_str and (end is None or date_str < end):
                return True
        return False

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
        if quarantine:
            common_idx = [s for s in common_idx if not _is_quarantined(s, date_str)]
        pbr = price[common_idx] / bps[common_idx]

        # PBR 하위 N% 매수 (값이 작을수록 저평가)
        num_target = int(len(pbr) * top_percentile)
        return pbr.nsmallest(num_target).index.tolist()

    return signal_func
