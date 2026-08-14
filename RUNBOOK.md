# Rebalance runbook

Manual procedure for the monthly rebalance. Follow it in order; the steps
that look redundant are the ones that catch mistakes.

## Before the market opens

- [ ] Confirm the machine will stay awake through the session
- [ ] `cd ~/dev/quant-lab && source .venv/bin/activate`
- [ ] `git status` — working tree clean
- [ ] `pytest` — all green

## Timing

Trade between **10:00 and 14:00**.

Avoid the first thirty minutes: liquidity providers are not obliged to
quote at the open, so the book can be thin exactly when the script reads
it. Avoid the last hour, and never trade after 15:20 — the closing
auction has no continuous matching, and a resting order can fill far from
the last traded price.

## Procedure

1. **Clear the candle cache.** A cache written earlier in the day holds
   an unfinished candle for today.

       rm -rf data/candles

2. **Dry run first.** Read the plan and sanity-check it: are the sells the
   symbols that dropped out of the top three, are the quantities plausible
   against the account size, are the prices near the current market?

       grep DRY_RUN .env          # expect true
       python rebalance_run.py

3. **Unlock.** Edit `.env`, set `TOSS_DRY_RUN=false`.

4. **Verify the unlock took effect.**

       python -c "from toss_client import TossClient; print(TossClient().dry_run)"

   Expect `False`. If it still prints `True`, the file was not saved.

5. **Run for real.**

       python rebalance_run.py

   Two confirmations are requested: the full plan, then the buy plan
   recomputed against the cash the sells actually raised. Read both.

6. **Lock again immediately.** Set `TOSS_DRY_RUN=true` and verify as in
   step 4. Do this before anything else, including checking results.

## After

- [ ] `sqlite3 -header -column data/quant.db "SELECT symbol, side, quantity, limit_price, filled, filled_qty, avg_fill_price FROM orders ORDER BY id;"`
- [ ] Confirm no orders are left resting: the script cancels on failure,
      but check the app as well
- [ ] Compare holdings in the app against the target: three names, roughly
      equal value
- [ ] `git status` — nothing unexpected

## If something goes wrong

**The script dies partway.** Nothing needs undoing. Re-running recomputes
from current holdings, so a partial state is simply the new starting
point. Cancel any resting orders first — the script does this at startup.

**An order will not fill.** After three attempts the script gives up and
logs it. The position stays off target until the next run; this is
preferable to chasing the price.

**`ExecutionError: abnormal book`.** The touch is more than 2% from the
last trade. Do not override it. Check the symbol in the app, and if the
book really is that wide, skip the rebalance for the day.

**Anything unclear.** Stop and cancel from the app. A missed rebalance
costs almost nothing; a wrong one is real money.

## Notes

- `ordered_today` blocks a second rebalance on the same trading date. It
  reads the `orders` table, so dry runs must not be recorded there.
- The universe is frozen until November. Do not adjust it on the day.