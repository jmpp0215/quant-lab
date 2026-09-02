import os
import sys
import sqlite3
import logging
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from quant.pead.dart import batch_fetch_with_checkpoint

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("backfill_dart")

def main():
    # KOSPI 전체 종목 가져오기 (가장 최근 영업일 기준)
    # yyyymmdd 형식으로 오늘 날짜 가져오기
    from datetime import datetime
    today_str = datetime.today().strftime("%Y%m%d")
    
    # KRX 봇 차단 이슈로 인해 pykrx 대신 FinanceDataReader 사용
    import FinanceDataReader as fdr
    
    # 빠른 데모 분석을 위해 임시로 시총 상위 50종목 수집
    kospi_df = fdr.StockListing('KOSPI')
    kospi_tickers = kospi_df['Code'].tolist()
    log.info(f"KOSPI Tickers count: {len(kospi_tickers)} (KOSPI ALL)")
    
    # 2020년부터 2023년까지 수집 (4년치)
    target_years = ["2020", "2021", "2022", "2023"]
    report_codes = ["11013", "11012", "11014", "11011"]
    
    for year in target_years:
        log.info(f"=== Starting backfill for year {year} ===")
        batch_fetch_with_checkpoint(kospi_tickers, year, report_codes)

if __name__ == "__main__":
    main()
