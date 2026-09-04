import sys
import logging
import subprocess
from datetime import datetime
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from quant.stock_factors.scripts import pbr_storage
from quant.toss_client import TossClient
from quant import accounts, market

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("check_pbr_due")

def main():
    pbr_storage.init()
    
    with pbr_storage.connect() as conn:
        last_rebalance_date = pbr_storage.get_latest_rebalance_date(conn)
        
    now = datetime.now().astimezone()
    today_str = now.strftime("%Y-%m-%d")
    
    # 1. Basic market open checks
    client = TossClient()
    calendar = client.market_calendar("KR")
    
    if not market.is_business_day(calendar):
        log.info("Market closed today.")
        return 0
        
    if last_rebalance_date == today_str:
        log.info(f"Already rebalanced today ({today_str}).")
        return 0
        
    # 2. Check if 20 trading days have passed
    from quant import candles
    history = candles.get(client, "005930", days=60, include_today=True)
    trading_dates = [c["timestamp"][:10] for c in history]
    
    if today_str not in trading_dates:
        log.error(f"Today {today_str} is not in trading dates list.")
        return 1
        
    if last_rebalance_date is None:
        log.info("No previous rebalance found. Rebalancing is DUE today (Initial execution).")
        due = True
    else:
        try:
            today_idx = trading_dates.index(today_str)
            last_idx = trading_dates.index(last_rebalance_date)
            days_passed = today_idx - last_idx
            log.info(f"Last rebalance: {last_rebalance_date} ({days_passed} trading days ago).")
            due = days_passed >= 20
        except ValueError:
            log.warning("Last rebalance date is too old or missing from recent history. Rebalancing is DUE.")
            due = True
            
    if not due:
        log.info("Not due for rebalance yet.")
        return 0
        
    # 3. Trigger Rebalance
    log.info("Triggering run_pbr_live.py --auto...")
    
    script_path = Path(__file__).parent / "run_pbr_live.py"
    
    cmd = [
        sys.executable,
        str(script_path),
        "--auto"
    ]
    
    # The cron environment should have TOSS_DRY_RUN=false if they actually want it to trade automatically.
    # Otherwise, it runs in dry_run mode as a dry-run test of the automation.
    log.info(f"Executing: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=False)
        log.info("run_pbr_live.py completed successfully.")
    except subprocess.CalledProcessError as e:
        log.error(f"run_pbr_live.py failed with exit code {e.returncode}")
        return 1
        
    return 0

if __name__ == "__main__":
    sys.exit(main())
