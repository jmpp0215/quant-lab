# Rebalance runbook

Manual procedure for the monthly rebalance. Follow it in order; the steps
that look redundant are the ones that catch mistakes.

**Status:** `kis-isa` is the primary strategy account (the default account
for every script) and is now live - the ISA go-live checklist below is
complete as of 2026-08-31, including a live verification of
`executor.execute()`'s retry/reprice/wait-for-fill loop against KIS, and
the `account != "toss-bot"` guard has been removed from `rebalance_run.py`.
The procedure below sends real orders against `kis-isa`. `toss-bot` remains
the sandbox account for ad hoc execution-path trials, off the regular
tranche schedule.

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

The `account != "toss-bot"` guard that used to stop step 5 short of sending
anything for `kis-isa` has been removed (see the go-live checklist below) -
step 5 now sends real orders for `kis-isa` like any other account.

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

## ISA go-live checklist (complete 2026-08-31)

`kis-isa` is the primary account by default. `rebalance_run.py` used to
refuse to place real orders for any account other than `toss-bot` (the
`account != "toss-bot"` guard) until every step below was done, in order.
All five are now complete and the guard has been removed - kept here as
the record of how that happened.

1. **Finish liquidating the legacy holdings.** `0074K0` and `현대차`
   predate the strategy and aren't part of `UNIVERSE`. Confirm the
   account holds none of them (`python show.py portfolio --account
   kis-isa` or the app) before seeding tranche books - `tranche_init.py`'s
   split has to account for every share it sees. Done 2026-08-31, confirmed
   live via `daily.py`'s reconcile check (`005380` and the other five
   legacy symbols all drifted to zero against the live account).
2. **Record the opening cash balance.**

       python cashflow.py --account kis-isa <date> <amount> "initial funding" --opening

   Without this, `daily.py`'s unexplained-cash-change check reads the
   liquidation proceeds as an unexplained cash movement every day. Not yet
   done as of 2026-08-31 - the day's sale proceeds hadn't settled yet, so
   `daily.py`'s check (which reads the `cash` field, not `total`) hadn't
   flagged it either. Do this once settlement lands and the true amount is
   known; it does not block order placement, only that daily alert.
3. **Seed the tranche books.**

       python tranche_init.py --account kis-isa

   Done 2026-08-31 with `--force` (books had been seeded once already, on
   2026-08-21, while the legacy holdings were still there - that seeding
   predated step 1 and needed redoing once liquidation actually finished).
4. **Verify `executor.execute()`'s retry loop live against KIS.** Done
   2026-08-31. `kis_client.open_orders()`/`place_order()`/`cancel()` were
   verified live 2026-08-24 (see below), but only by calling them
   directly. On 2026-08-31, `executor.execute()` itself was exercised
   live against `kis-isa` twice, both via a throwaway script
   (`verify_executor_live.py`, since deleted) rather than
   `rebalance_run.py`:
   - A 1-share `497880` BUY priced normally (through the touch) filled
     immediately, confirming the fill-detection and `fetch_execution()`
     parsing path.
   - A second 1-share `497880` BUY, with `limit_price_for` overridden for
     that run only to price one tick *behind* the best bid (so the order
     joined the book instead of crossing the spread) and `FILL_TIMEOUT`
     dropped to 1s, forced two full timeout -> `cancel()` ->
     `fetch_execution()` -> reprice cycles before giving up
     (`MAX_ATTEMPTS=2`), both cancelled cleanly with nothing left resting.
   Together this exercised both branches of the retry loop - immediate
   fill and repeated timeout/cancel/reprice - that individual client-call
   testing on 2026-08-24 did not touch.
5. **Remove the guard.** Done 2026-08-31: deleted the
   `account != "toss-bot"` guard in `rebalance_run.py` (both the
   `cancel_open_orders` skip and the block before sending) and
   `cancel_open_orders` now runs unconditionally.

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
this reason. It's no longer held (step 1 above), but stays in `HELD_ETFS`
- `tests/test_market.py` pins it as the regression case for this bug, so
removing the entry needs updating those tests too, not just checking
current holdings. Add to `HELD_ETFS` before trading any other outside
holding.

## toss-bot (sandbox)

`toss-bot` is no longer on the regular tranche schedule - it exists for
deliberate, ad hoc trials when changing execution code, using the same
dry-run discipline as the procedure above (`TOSS_DRY_RUN` /
`TossClient()`). Both accounts place real orders now, but `toss-bot` is
still the lower-stakes place to try an execution-path change against real
fills before trusting it on `kis-isa`. Invoke every script with
`--account toss-bot` explicitly - it is no longer the default.

## Starting a new account, or adding/removing a tranche

`tranche_value` sizes a sleeve as *its own holdings* + 1/N of the cash
pool. That only equals 1/N of the whole account when every sleeve already
holds roughly 1/N of the equity. **Seed all sleeves in one shot** so that
is true from the first rebalance:

    python tranche_init.py --account <acct> [--force]

Do **not** let sleeves fill in one at a time on their scheduled days from a
cash-heavy account. An empty sleeve sizes itself to `cash / N`, which is
far below `account / N` while the other sleeves hold the equity; as those
sleeves buy, the remaining cash shrinks and every not-yet-seeded sleeve's
target shrinks with it. Late sleeves never reach 1/N and the account stays
under-deployed - a self-reinforcing bias, not a one-off.

Seen live on `kis-isa` (2026-09): tranche 0 was seeded 09-01, then tranche
5 ran 09-10 from an empty book and sized to `cash/3` - about a third under
its intended share; tranche 10 would have compounded it. Fixed 2026-09-10
by re-running `tranche_init.py --account kis-isa --force` to split the
then-current holdings (188/28/7/105 across 은행/코스피200/나스닥100/유로스탁스)
evenly across tranches 0/5/10, then confirming `tranche.reconcile` came
back empty against the live account.

After any `tranche_init.py` run, verify the books tie out:

    python -c "from quant import accounts, storage, tranche; \
      cfg=accounts.resolve('<acct>'); snap=cfg['snapshot'](cfg['client']()); \
      actual={p['symbol']:p['qty'] for p in snap.positions}; \
      books=storage.load_all_tranche_holdings(storage.connect(),'<acct>'); \
      print(tranche.reconcile(books, actual) or 'CLEAN')"

`backtest.run_tranched` already seeds every sleeve up front (its `initial /
n` split), so a backtest will not show this drift even though live can -
keep that in mind when comparing the two.

## Notes

- `ordered_today` blocks a second rebalance on the same trading date. It
  reads the `orders` table, so dry runs must not be recorded there.
- The universe is frozen until November. Do not adjust it on the day.
- Toss issues **one active token per credential set**: authenticating a
  second `TossClient` silently invalidates the first one's token. Entry
  points reuse a single instance rather than creating one per role.