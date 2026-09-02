import json
import os
import sys
import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
from pykrx import stock

sys.path.append(str(Path(__file__).parent.parent.parent.parent))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("backfill_prices")

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "quant.db"
CHECKPOINT_FILE = Path(__file__).parent.parent.parent.parent / "data" / "dart_cache" / "price_backfill_checkpoint.json"


def _atomic_write_json(path: Path, data: dict):
    """임시 파일에 쓴 뒤 원자적으로 rename하여, 쓰기 도중 강제종료되어도 체크포인트 파일이
    손상되지 않게 합니다 (dart.py의 batch_fetch_with_checkpoint와 동일한 방식)."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, 'w') as f:
        json.dump(data, f)
    os.replace(tmp_path, path)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    from quant.pead.storage import init_db as storage_init
    storage_init() # Ensures schemas are created
    return conn

def main():
    conn = init_db()
    cursor = conn.cursor()
    
    import FinanceDataReader as fdr
    
    start_date = "2020-01-01"
    end_date = datetime.today().strftime("%Y-%m-%d")
    
    kospi_df = fdr.StockListing('KOSPI')
    kospi_tickers = kospi_df['Code'].tolist()

    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    completed = set()
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE, 'r') as f:
            completed = set(json.load(f).get("completed", []))
    log.info(f"Loaded checkpoint. {len(completed)} tickers already fetched.")

    log.info(f"Starting price backfill for {len(kospi_tickers)} tickers from {start_date} to {end_date}")

    for ticker in kospi_tickers:
        if ticker in completed:
            continue

        log.info(f"Fetching prices for {ticker}...")
        try:
            df = fdr.DataReader(ticker, start_date, end_date)
            if df.empty:
                completed.add(ticker)
                _atomic_write_json(CHECKPOINT_FILE, {"completed": list(completed)})
                continue

            records = []
            for date_idx, row in df.iterrows():
                dt_str = date_idx.strftime('%Y%m%d')
                close_price = float(row['Close'])
                volume = float(row['Volume'])
                
                # fdr returns open, high, low, close, volume, change.
                # Trading value isn't provided directly, so we approximate as Volume * Close for now
                trading_value = volume * close_price
                
                records.append((
                    dt_str,
                    ticker,
                    float(row['Open']),
                    float(row['High']),
                    float(row['Low']),
                    close_price,
                    volume,
                    trading_value
                ))
                
            cursor.executemany("""
                INSERT OR REPLACE INTO pead_price_raw 
                (date, symbol, open, high, low, close, volume, trading_value)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, records)
            conn.commit()

            completed.add(ticker)
            _atomic_write_json(CHECKPOINT_FILE, {"completed": list(completed)})

        except Exception as e:
            log.error(f"Error fetching {ticker}: {e}")

    log.info("Price backfill completed.")
    conn.close()

if __name__ == "__main__":
    main()
