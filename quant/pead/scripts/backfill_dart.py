import sys
import logging
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from quant.pead.dart import batch_fetch_with_checkpoint

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("backfill_dart")

def main():
    # FinanceDataReader passes no timeout - see quant/common/http_timeout.py.
    from quant.common import http_timeout
    http_timeout.install()
    
    # KRX 봇 차단 이슈로 인해 pykrx 대신 FinanceDataReader 사용
    import FinanceDataReader as fdr
    
    # KOSPI ALL
    from quant.pead.universe import kospi_history_sources
    kospi_tickers = list(kospi_history_sources(fdr, "2020-01-01", date.today().isoformat()))
    
    # 팩터 시뮬레이션 시 시가총액 편향을 막기 위해 무작위로 섞어서 수집 (API 한도 도달 대비)
    import random
    random.seed(42)
    random.shuffle(kospi_tickers)
    
    log.info(f"KOSPI Tickers count: {len(kospi_tickers)} (KOSPI ALL, Shuffled)")
    
    # 2020년부터 2026년까지 수집
    target_years = ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]
    report_codes = ["11013", "11012", "11014", "11011"]
    
    for year in target_years:
        log.info(f"=== Starting backfill for year {year} ===")
        batch_fetch_with_checkpoint(kospi_tickers, year, report_codes)

if __name__ == "__main__":
    main()
