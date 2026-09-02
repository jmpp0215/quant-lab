"""SQLite schema for PEAD strategy in quant.db."""

PEAD_SCHEMA = """
-- 원본 공시 데이터 (정정 공시 덮어쓰기 금지, config-agnostic 전체 저장)
-- metric: operating_income, net_income, eps, operating_cash_flow
CREATE TABLE IF NOT EXISTS pead_dart_raw (
    symbol TEXT NOT NULL,
    target_year TEXT NOT NULL,
    rcept_dt TEXT NOT NULL,
    report_code TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL,
    basis TEXT NOT NULL,
    is_correction INTEGER DEFAULT 0,
    original_rcept_dt TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, target_year, rcept_dt, report_code, metric, basis, is_correction)
);

-- YTD 누적 값을 단일 분기 값으로 환산한 테이블
CREATE TABLE IF NOT EXISTS pead_quarterly_normalized (
    symbol TEXT NOT NULL,
    target_year TEXT NOT NULL,
    rcept_dt TEXT NOT NULL,
    report_code TEXT NOT NULL,
    metric TEXT NOT NULL,
    basis TEXT NOT NULL,
    quarterly_value REAL,
    is_estimable INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, target_year, rcept_dt, report_code, metric, basis)
);

-- OHLCV 및 거래대금(liquidity) 데이터 저장을 위한 테이블
CREATE TABLE IF NOT EXISTS pead_price_raw (
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    trading_value REAL,
    PRIMARY KEY (date, symbol)
);

-- 벤치마크(코스피 등) 지수 OHLCV. pead_price_raw와 분리해 개별 종목 유니버스 조회(예: DISTINCT
-- symbol)에 지수가 섞여 들어가는 것을 방지한다. index_symbol은 FinanceDataReader의 지수
-- 티커(예: 코스피 종합지수 'KS11')를 그대로 사용한다.
CREATE TABLE IF NOT EXISTS pead_benchmark_raw (
    date TEXT NOT NULL,
    index_symbol TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    trading_value REAL,
    PRIMARY KEY (date, index_symbol)
);

-- 1차 레이어: Surprise 계산 결과
-- lookback_quarters 등 metric/basis만으로는 구분되지 않는 config 값도 surprise_score에
-- 영향을 주므로 config_hash를 키에 포함해 config 조합별로 별도 저장한다.
-- is_estimable=0(계산 불가)인 경우 surprise_score는 NULL로 저장된다.
CREATE TABLE IF NOT EXISTS pead_surprises (
    symbol TEXT NOT NULL,
    rcept_dt TEXT NOT NULL,
    metric TEXT NOT NULL,
    basis TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    surprise_score REAL,
    is_estimable INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, rcept_dt, metric, basis, config_hash)
);

-- 2차 레이어: Quality Scores (enable 여부 무관하게 항상 저장)
CREATE TABLE IF NOT EXISTS pead_quality_scores (
    symbol TEXT NOT NULL,
    rcept_dt TEXT NOT NULL,
    ocf_to_oi_ratio REAL,
    passes_quality_filter INTEGER NOT NULL,
    PRIMARY KEY (symbol, rcept_dt)
);

-- 3차 레이어: Flow Scores (enable 여부 무관하게 항상 저장)
CREATE TABLE IF NOT EXISTS pead_flow_scores (
    symbol TEXT NOT NULL,
    rcept_dt TEXT NOT NULL,
    institutional_net_buy_ratio REAL,
    foreign_net_buy_ratio REAL,
    individual_net_buy_ratio REAL,
    PRIMARY KEY (symbol, rcept_dt)
);

-- 최종 레이어: Combined Scores
CREATE TABLE IF NOT EXISTS pead_combined_scores (
    symbol TEXT NOT NULL,
    rcept_dt TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    final_score REAL NOT NULL,
    PRIMARY KEY (symbol, rcept_dt, config_hash)
);

-- 이벤트 타임라인 (수익률 로그)
CREATE TABLE IF NOT EXISTS pead_event_returns (
    symbol TEXT NOT NULL,
    rcept_dt TEXT NOT NULL,
    entry_timing TEXT NOT NULL,
    entry_price REAL NOT NULL,
    return_5d REAL,
    return_10d REAL,
    return_20d REAL,
    return_40d REAL,
    return_60d REAL,
    PRIMARY KEY (symbol, rcept_dt, entry_timing)
);

-- 섹터 분류 (pykrx 기반)
CREATE TABLE IF NOT EXISTS pead_sector_map (
    symbol TEXT NOT NULL,
    sector_code TEXT NOT NULL,
    sector_name TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    PRIMARY KEY (symbol, snapshot_date)
);

-- 포지션 로그 (스켈레톤)
CREATE TABLE IF NOT EXISTS pead_portfolio_log (
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    weight REAL NOT NULL,
    PRIMARY KEY (trade_date, symbol)
);
"""

import sqlite3
import logging
from pathlib import Path

from .config import PeadConfig

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "data" / "quant.db"

def init_db():
    """Execute PEAD schemas to initialize tables in quant.db."""
    log.info("Initializing PEAD tables in %s", DB_PATH)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(PEAD_SCHEMA)


def save_surprise(
    symbol: str,
    rcept_dt: str,
    config: PeadConfig,
    surprise_score: float | None,
    is_estimable: bool,
):
    """signal.calculate_surprise()의 결과를 pead_surprises에 저장합니다.

    동일한 (symbol, rcept_dt, metric, basis) 조합이라도 lookback_quarters 등 다른 config 값에
    따라 surprise_score가 달라질 수 있으므로 config.get_hash()를 키에 포함해 config 조합별로
    별도 저장한다. is_estimable=False인 경우 surprise_score는 NULL로 저장된다.
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO pead_surprises
            (symbol, rcept_dt, metric, basis, config_hash, surprise_score, is_estimable)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                rcept_dt,
                config.earnings_metric,
                config.dart_basis,
                config.get_hash(),
                surprise_score,
                int(is_estimable),
            ),
        )


def save_quality_score(
    symbol: str,
    rcept_dt: str,
    ocf_to_oi_ratio: float | None,
    passes_quality_filter: bool,
):
    """quality.calculate_quality_score()의 결과를 pead_quality_scores에 저장합니다.

    (주의) pead_quality_scores의 기존 PK는 (symbol, rcept_dt)뿐이라 config_hash가 없습니다
    — 계산 불가능한 경우 ocf_to_oi_ratio=None을 저장하는 것은 pead_surprises와 동일하지만,
    dart_basis나 quality_threshold를 바꿔 다시 계산하면 이전 결과를 덮어씁니다. 이는 이
    함수가 새로 만든 제약이 아니라 기존 스키마의 한계이며, 이번 요청 범위에서는 스키마를
    바꾸지 않았습니다.
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO pead_quality_scores
            (symbol, rcept_dt, ocf_to_oi_ratio, passes_quality_filter)
            VALUES (?, ?, ?, ?)
            """,
            (symbol, rcept_dt, ocf_to_oi_ratio, int(passes_quality_filter)),
        )
