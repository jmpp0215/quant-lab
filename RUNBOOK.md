# Rebalance runbook

Manual procedure for the monthly rebalance. Follow it in order; the steps
that look redundant are the ones that catch mistakes.

**Status:** `kis-isa` is the primary strategy account (the default account
for every script). Real order placement is still gated to `toss-bot` only,
pending the live KIS verification in the go-live checklist below —
`toss-bot` is the sandbox account now: not on the regular tranche
schedule, but still the only account this script will actually trade live
until that checklist is done. Once it is, the procedure below runs
against `kis-isa` for real; until then, `rebalance_run.py` for `kis-isa`
computes and prints the plan and stops, same as today.

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

Written for `kis-isa` (the primary account, and the default if `--account`
is omitted). The mechanics are identical for `toss-bot` - substitute
`TOSS_DRY_RUN` / `TossClient()` for `KIS_DRY_RUN` / `KisClient("isa")`
below, and add `--account toss-bot` to the `rebalance_run.py` calls.

1. **Clear the candle cache.** A cache written earlier in the day holds
   an unfinished candle for today.

       rm -rf data/candles

2. **Dry run first.** Read the plan and sanity-check it: are the sells the
   symbols that dropped out of the top three, are the quantities plausible
   against the account size, are the prices near the current market?

       grep KIS_DRY_RUN .env      # expect true
       python rebalance_run.py

3. **Unlock.** Edit `.env`, set `KIS_DRY_RUN=false`.

4. **Verify the unlock took effect.**

       python -c "from quant.kis_client import KisClient; print(KisClient('isa').dry_run)"

   Expect `False`. If it still prints `True`, the file was not saved.

5. **Run for real.**

       python rebalance_run.py

   Two confirmations are requested: the full plan, then the buy plan
   recomputed against the cash the sells actually raised. Read both.

6. **Lock again immediately.** Set `KIS_DRY_RUN=true` and verify as in
   step 4. Do this before anything else, including checking results.

Until the ISA go-live checklist below is complete, step 5 stops at the
`account != "toss-bot"` guard for `kis-isa` - it logs the plan and
returns without sending anything, even unlocked. That is expected, not a
bug: real orders on `kis-isa` only start once the checklist's live
verification step passes and the guard is removed.

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

## ISA go-live checklist

`kis-isa` is the primary account by default, but `rebalance_run.py`
refuses to place real orders for any account other than `toss-bot` (the
`account != "toss-bot"` guard) until every step below is done, in order:

1. **Finish liquidating the legacy holdings.** `0074K0` and `현대차`
   predate the strategy and aren't part of `UNIVERSE`. Confirm the
   account holds none of them (`python show.py portfolio --account
   kis-isa` or the app) before seeding tranche books - `tranche_init.py`'s
   split has to account for every share it sees.
2. **Record the opening cash balance.**

       python cashflow.py --account kis-isa <date> <amount> "initial funding" --opening

   Without this, `daily.py`'s unexplained-cash-change check reads the
   liquidation proceeds as an unexplained cash movement every day.
3. **Seed the tranche books.**

       python tranche_init.py --account kis-isa

4. **Verify `executor.execute()`'s retry loop live against KIS.** Not yet
   done. `kis_client.open_orders()`/`place_order()`/`cancel()` were
   verified live 2026-08-24 (see below), but only by calling them
   directly - `executor.execute()`'s retry/reprice/wait-for-fill loop,
   which handles partial fills and reprices through the touch, has never
   run against a real KIS order. Exercise it deliberately: a small order
   priced to rest, then reprice/fill, run through `executor.execute()`
   itself rather than the client methods directly.
5. **Remove the guard.** Once step 4 passes, delete the
   `account != "toss-bot"` guard in `rebalance_run.py` (both the
   `cancel_open_orders` skip and the block before sending) and re-enable
   `cancel_open_orders` unconditionally. This is the step that turns on
   live ISA trading - do it deliberately, not as a side effect of an
   unrelated change.

**`kis_client.open_orders()`'s response shape is confirmed.** Verified
2026-08-24 against ISA: a real 1-share 102110 order, priced 15% below
the bid to guarantee it would rest, was placed via
`kis_client.place_order()`, read back correctly by `open_orders()`
(`odno`/`ord_gno_brno`/`ord_qty`/`tot_ccld_qty` matched exactly what
was predicted), and cancelled via `kis_client.cancel()` - confirmed
gone on a second `open_orders()` call. KIS has no paper environment, so
this was also the first real order this code has ever sent - it went
through cleanly.

An ETF held outside `UNIVERSE`/`WATCH_ONLY` must be listed in
`config.HELD_ETFS`, or `is_etf()` prices it on the stock tick grid: an
order is then refused as off-tick about half the time and silently priced
a tick too far through the touch the other half. `0074K0` is there for
this reason (remove it once step 1 above is done and it's no longer
held). Add to `HELD_ETFS` before trading any other outside holding.

## toss-bot (sandbox)

`toss-bot` is no longer on the regular tranche schedule - it exists for
deliberate, ad hoc trials when changing execution code, using the same
dry-run discipline as the procedure above (`TOSS_DRY_RUN` /
`TossClient()`). It remains the only account this script currently
places real orders for, so it's still the account to use for testing an
execution-path change against real fills before trusting it on `kis-isa`.
Invoke every script with `--account toss-bot` explicitly - it is no
longer the default.

## Notes

- `ordered_today` blocks a second rebalance on the same trading date. It
  reads the `orders` table, so dry runs must not be recorded there.
- The universe is frozen until November. Do not adjust it on the day.
- Toss issues **one active token per credential set**: authenticating a
  second `TossClient` silently invalidates the first one's token. Entry
  points reuse a single instance rather than creating one per role.