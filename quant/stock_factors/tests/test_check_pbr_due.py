from quant.stock_factors.scripts.check_pbr_due import compute_due_status

# 25 trading days, 2026-08-17 .. 2026-09-18 (weekdays only, no holidays needed
# for this fixture - only relative trading-day offsets matter).
TRADING_DATES = [f"2026-08-{d:02d}" for d in range(17, 32) if d not in (22, 23, 29, 30)]
TRADING_DATES += [f"2026-09-{d:02d}" for d in range(1, 19) if d not in (5, 6, 12, 13)]


def _clean(trade_date):
    return {"trade_date": trade_date, "all_clear": True, "failed": [], "partial": [], "target_portfolio": {}}


def _incomplete(trade_date, failed=None, partial=None):
    return {
        "trade_date": trade_date, "all_clear": False,
        "failed": failed or ["000001"], "partial": partial or [],
        "target_portfolio": {"000001": 1.0},
    }


def test_no_previous_rebalance_is_due_full():
    due_full, needs_retry = compute_due_status(None, None, TRADING_DATES, TRADING_DATES[0])
    assert due_full is True
    assert needs_retry is False


def test_clean_rebalance_within_20_days_is_not_due():
    full_date = TRADING_DATES[0]
    today = TRADING_DATES[5]  # 5 trading days later
    due_full, needs_retry = compute_due_status(_clean(full_date), full_date, TRADING_DATES, today)
    assert due_full is False
    assert needs_retry is False


def test_incomplete_rebalance_within_retry_window_needs_retry():
    full_date = TRADING_DATES[0]
    today = TRADING_DATES[2]  # 2 trading days later, within RETRY_WINDOW_TRADING_DAYS=3
    latest = _incomplete(full_date)
    due_full, needs_retry = compute_due_status(latest, full_date, TRADING_DATES, today)
    assert due_full is False
    assert needs_retry is True


def test_incomplete_rebalance_beyond_retry_window_gives_up_and_waits():
    full_date = TRADING_DATES[0]
    today = TRADING_DATES[10]  # 10 trading days later - past the 3-day retry window, short of 20
    latest = _incomplete(full_date)
    due_full, needs_retry = compute_due_status(latest, full_date, TRADING_DATES, today)
    assert due_full is False
    assert needs_retry is False


def test_20_trading_days_elapsed_is_due_full_regardless_of_retry_history():
    full_date = TRADING_DATES[0]
    today = TRADING_DATES[20]  # exactly 20 trading days later
    # Even though the last rebalance (a retry a few days after the full one)
    # was itself incomplete, the full-cycle clock is unaffected by retries -
    # a fresh full reconstruction takes priority.
    latest = _incomplete(TRADING_DATES[3])
    due_full, needs_retry = compute_due_status(latest, full_date, TRADING_DATES, today)
    assert due_full is True


def test_retry_of_a_retry_still_paces_off_the_original_full_rebalance():
    """get_latest_full_rebalance_date always resolves to the ORIGINAL full
    rebalance, never an intermediate retry row - compute_due_status doesn't
    need to know about retry chains, only the (latest, full_date) pair it's
    handed."""
    full_date = TRADING_DATES[0]
    today = TRADING_DATES[1]
    latest = _incomplete(TRADING_DATES[0])  # the retry attempt itself also failed
    due_full, needs_retry = compute_due_status(latest, full_date, TRADING_DATES, today)
    assert due_full is False
    assert needs_retry is True
