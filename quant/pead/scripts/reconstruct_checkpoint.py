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
    
    for symbol, target_year, report_code, metric in rows:
        key = f"{symbol}_{target_year}_{report_code}"
        if metric in ('operating_income', 'net_income', 'total_assets', 'total_equity', 'revenue'):
            fin_completed.add(key)
        elif metric == 'operating_cash_flow':
            cf_completed.add(key)
        elif metric == 'issued_shares':
            shares_completed.add(key)
        elif metric == 'gross_profit':
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
