import sys
import logging
import subprocess
from datetime import datetime
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from quant.stock_factors.scripts import pbr_storage
from quant.toss_client import TossClient
from quant import market

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("check_pbr_due")

FULL_REBALANCE_TRADING_DAYS = 20

# 부분/실패 리밸런싱 이후, 실패한 종목만 소규모로 재시도할 수 있는 기간(거래일). 이
# 기간을 넘기면 재시도를 포기하고 다음 20거래일 전체 사이클을 기다린다 - 몇 종목만
# 매번 체결이 안 되는 패턴이 반복돼도 전체 포트폴리오가 매일 재구성되는 걸 막기 위한
# 상한이다.
RETRY_WINDOW_TRADING_DAYS = 3


def compute_due_status(latest: dict | None, full_date: str | None,
                       trading_dates: list[str], today_str: str) -> tuple[bool, bool]:
    """Pure decision logic, split out of main() for testability (no network
    calls). Returns (due_full, needs_retry) for today:

    - due_full: a full portfolio reconstruction is due (>= FULL_REBALANCE_TRADING_DAYS
      trading days since the last FULL rebalance - retries don't reset this clock).
    - needs_retry: the last rebalance was incomplete (all_clear=False) and we're
      still within RETRY_WINDOW_TRADING_DAYS trading days of the full rebalance
      it belongs to, so a lightweight retry (same target, no re-ranking) is due
      instead of waiting for the next full cycle.

    due_full takes priority when both would fire (a fresh full reconstruction
    naturally reconsiders everything, including whatever didn't fill last time).
    """
    today_idx = trading_dates.index(today_str)

    if full_date is None:
        due_full = True
    else:
        try:
            full_idx = trading_dates.index(full_date)
            due_full = (today_idx - full_idx) >= FULL_REBALANCE_TRADING_DAYS
        except ValueError:
            due_full = True

    needs_retry = False
    if not due_full and latest is not None and not latest["all_clear"] and full_date is not None:
        try:
            full_idx = trading_dates.index(full_date)
            needs_retry = (today_idx - full_idx) <= RETRY_WINDOW_TRADING_DAYS
        except ValueError:
            needs_retry = False

    return due_full, needs_retry


def main():
    pbr_storage.init()

    with pbr_storage.connect() as conn:
        latest = pbr_storage.get_latest_rebalance(conn)
        full_date = pbr_storage.get_latest_full_rebalance_date(conn)

    now = datetime.now().astimezone()
    today_str = now.strftime("%Y-%m-%d")

    # 1. Basic market open checks
    client = TossClient()
    calendar = client.market_calendar("KR")

    if not market.is_business_day(calendar):
        log.info("Market closed today.")
        return 0

    if latest is not None and latest["trade_date"] == today_str:
        log.info(f"Already rebalanced today ({today_str}).")
        return 0

    # 2. Check if 20 trading days have passed since the last FULL rebalance
    # (retry attempts don't count - see get_latest_full_rebalance_date)
    from quant import candles
    history = candles.get(client, "005930", days=60, include_today=True)
    trading_dates = [c["timestamp"][:10] for c in history]

    if today_str not in trading_dates:
        log.error(f"Today {today_str} is not in trading dates list.")
        return 1

    if full_date is None:
        log.info("No previous full rebalance found. Rebalancing is DUE today (Initial execution).")
    else:
        log.info(f"Last full rebalance: {full_date}.")

    due_full, needs_retry = compute_due_status(latest, full_date, trading_dates, today_str)

    if not due_full and not needs_retry and latest is not None and not latest["all_clear"]:
        log.info(
            f"Last rebalance ({latest['trade_date']}) was incomplete "
            f"(failed={latest['failed']}, partial={latest['partial']}), but the "
            f"{RETRY_WINDOW_TRADING_DAYS}-trading-day retry window has passed. "
            "Waiting for the next full cycle."
        )

    if not due_full and not needs_retry:
        log.info("Not due for rebalance yet.")
        return 0

    # 4. Trigger Rebalance (full reconstruction, or a retry of just the
    # still-incomplete symbols from the last full rebalance)
    script_path = Path(__file__).parent / "run_pbr_live.py"

    cmd = [
        sys.executable,
        str(script_path),
        "--auto"
    ]
    if needs_retry and not due_full:
        log.info(
            f"Retrying incomplete rebalance from {latest['trade_date']} "
            f"(failed={latest['failed']}, partial={latest['partial']})..."
        )
        cmd.append("--retry")
    else:
        log.info("Triggering full rebalance via run_pbr_live.py --auto...")

    # The cron environment should have TOSS_DRY_RUN=false if they actually want it to trade automatically.
    # Otherwise, it runs in dry_run mode as a dry-run test of the automation.
    log.info(f"Executing: {' '.join(cmd)}")
    
    try:
        subprocess.run(cmd, check=True, capture_output=False)
        log.info("run_pbr_live.py completed successfully.")
    except subprocess.CalledProcessError as e:
        log.error(f"run_pbr_live.py failed with exit code {e.returncode}")
        return 1
        
    return 0

if __name__ == "__main__":
    sys.exit(main())
