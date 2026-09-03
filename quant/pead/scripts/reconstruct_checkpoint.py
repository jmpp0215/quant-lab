import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "quant.db"
CHECKPOINT_FILE = Path(__file__).parent.parent.parent.parent / "data" / "dart_cache" / "fetch_checkpoint.json"

def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    fin_completed = set()
    cf_completed = set()
    shares_completed = set()
    gp_completed = set()
    
    cursor.execute("SELECT DISTINCT symbol, target_year, report_code, metric FROM pead_dart_raw")
    rows = cursor.fetchall()
    
    # First, collect metrics per key
    metrics_by_key = {}
    for symbol, target_year, report_code, metric in rows:
        key = f"{symbol}_{target_year}_{report_code}"
        if key not in metrics_by_key:
            metrics_by_key[key] = set()
        metrics_by_key[key].add(metric)
        
    for key, metrics in metrics_by_key.items():
        # fin_completed ONLY if total_equity is present (meaning new fetch was done)
        if 'total_equity' in metrics:
            fin_completed.add(key)
        
        if 'operating_cash_flow' in metrics:
            cf_completed.add(key)
        if 'issued_shares' in metrics:
            shares_completed.add(key)
        if 'gross_profit' in metrics:
            gp_completed.add(key)
            
    conn.close()
    
    checkpoint = {
        "fin_completed": list(fin_completed),
        "cf_completed": list(cf_completed),
        "shares_completed": list(shares_completed),
        "gp_completed": list(gp_completed),
    }
    
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(checkpoint, f)
        
    print(f"Reconstructed checkpoint: FIN {len(fin_completed)}, CF {len(cf_completed)}, SHARES {len(shares_completed)}, GP {len(gp_completed)}")

if __name__ == "__main__":
    main()
