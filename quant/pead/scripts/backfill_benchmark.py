import sys
import sqlite3
import logging
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).parent.parent.parent.parent))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("backfill_benchmark")

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "quant.db"

# FinanceDataReader의 코스피 종합지수 티커. pykrx의 get_index_ohlcv는 이 환경에서
# KRX 웹사이트 접근이 막혀 있어(backfill_prices.py와 동일한 이유) 대신 FDR을 사용한다.
KOSPI_INDEX_TICKER = "KS11"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    from quant.pead.storage import init_db as storage_init
    storage_init()  # Ensures schemas are created
    return conn


def main():
    conn = init_db()
    cursor = conn.cursor()

    import FinanceDataReader as fdr

    start_date = "2020-01-01"
    end_date = datetime.today().strftime("%Y-%m-%d")

    log.info(f"Fetching KOSPI({KOSPI_INDEX_TICKER}) index OHLCV from {start_date} to {end_date}...")
    df = fdr.DataReader(KOSPI_INDEX_TICKER, start_date, end_date)

    if df.empty:
        log.error("No KOSPI index data returned.")
        conn.close()
        return

    records = []
    for date_idx, row in df.iterrows():
        dt_str = date_idx.strftime('%Y%m%d')
        records.append((
            dt_str,
            KOSPI_INDEX_TICKER,
            float(row['Open']),
            float(row['High']),
            float(row['Low']),
            float(row['Close']),
            float(row['Volume']),
            float(row['Amount']),
        ))

    cursor.executemany("""
        INSERT OR REPLACE INTO pead_benchmark_raw
        (date, index_symbol, open, high, low, close, volume, trading_value)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, records)
    conn.commit()
    conn.close()

    log.info(f"Benchmark backfill completed: {len(records)} rows for {KOSPI_INDEX_TICKER}.")


if __name__ == "__main__":
    main()
