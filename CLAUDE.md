# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A dual momentum trading system built on the Toss Securities and KIS (한국투자증권) Open
APIs. Ranks a universe of eight Korea-listed ETFs by risk-adjusted momentum and
rebalances a live brokerage account monthly, in staggered tranches. Personal research
project, not a library — there is no paper trading environment, so all safety comes from
code structure, not from a sandbox.

Trading is multi-account (`quant/accounts.py`): `kis-isa` (a KIS ISA account) is the
primary strategy account and the default for every script; `toss-bot` (Toss) is a
sandbox account for ad hoc execution-path trials, off the regular tranche schedule;
`kis-main`/`kis-main-overseas` are observed (snapshotted daily) but never traded by
this strategy. See `RUNBOOK.md` for the current live-trading status per account.

## Commands

```bash
source .venv/bin/activate    # Python 3.14, venv already present
pytest                       # run the full suite
pytest test_rebalance.py     # one file
pytest test_rebalance.py::TestOrdering::test_sells_before_buys   # one test
ruff check .                 # lint (F, E9, B, DTZ, I — see pyproject.toml)
ruff check . --fix

python check_setup.py        # read-only API connectivity check
python daily.py               # compute and record today's signal (no orders)
python check_due.py           # is a tranche scheduled today? desktop notification only
python rebalance_run.py       # interactive: place real orders for the tranche due today
python tranche_init.py        # one-time: assign existing holdings to tranches
python show.py                # inspect recorded history
python slippage.py            # analyze execution slippage vs limit price
```

Tests are plain `pytest` classes/functions per module (`TestXxx` grouping cases), no
custom fixtures beyond `test_executor.py`'s `FakeClient`. New arithmetic-heavy logic
(order sizing, weighting, tranche math) should get case-by-case tests the way
`test_rebalance.py` and `test_tranche.py` do — this code moves real money.

## Architecture

**Pure core, imperative shell.** `strategy.py` (`evaluate`) and `rebalance.py` (`plan`) are
pure functions: candles/positions/prices in, a `Signal`/list of `Order` out — no network,
clock, or filesystem. This is deliberate so the same code path drives both live trading and
backtesting. When changing signal or order logic, keep new dependencies (config, other pure
modules) — never `TossClient`, `datetime.now()`, or disk I/O — out of these two files.

**Everything tunable lives in `config.py`**: the universe, dividend yield estimates, spread
estimates, tax treatment, cash proxy symbol, `TOP_N`, lookback/skip months, tranche schedule.
Changing strategy behavior almost always means editing constants here, not logic elsewhere.

**Module responsibilities:**
| Module | Responsibility |
|---|---|
| `accounts.py` | Account registry (`toss-bot`, `kis-isa`, `kis-main`, `kis-main-overseas`) — which client/broker/snapshot backs each, `--account` flag parsing, default account |
| `broker.py` | The execution interface every broker module (`toss_client`, `kis_client`) implements — dataclasses only, no logic |
| `toss_client.py` | REST client for Toss — token caching, 429 backoff, error envelope parsing |
| `kis_client.py` | REST client for KIS (한국투자증권) — backs `kis-isa`/`kis-main`; domestic + overseas endpoints, per-account credentials |
| `candles.py` | Daily candle fetching — Toss default, KIS optional via `source="kis"` (rows normalised to Toss field names); per-source disk cache (`data/candles`, `data/candles/kis`), excludes in-progress candle |
| `dividends.py` | Distribution-event fetching from KIS + incremental SQLite cache (`dividend_events`/`dividend_fetch_state`) — imperative-shell counterpart to `momentum.py`'s dividend math |
| `momentum.py` | Date-anchored lookback returns with dividend adjustment (pure — `trailing_yield()` takes cached events in, never fetches) |
| `indicators.py` | Technical indicators (e.g. moving-average trend filter) used by strategy variants |
| `allocation.py` | Covariance/risk-parity/inverse-vol weighting math |
| `strategy.py` | Dual momentum signal generation (pure) + recorded-but-unused allocation variants |
| `rebalance.py` | Target weights → orders: share rounding, tick-size snapping, min order size |
| `tranche.py` | Splits capital into staggered sleeves; per-sleeve books, schedule, drift reconciliation |
| `executor.py` | Places limit orders at the touch, polls fills, retries remainder, enforces deviation/auction guards |
| `market.py` | Session detection, KRX tick sizes |
| `storage.py` | SQLite persistence (`data/quant.db`) — signals, orders, portfolio snapshots, tranche books, cashflows, all `account`-scoped except the shared signal tables |
| `config.py` | Universe and all strategy parameters |
| `daily.py` | Cron entry point: evaluate + record signal (once, for the strategy account), snapshot every account, never trades |
| `check_due.py` | Cron entry point: notify if a tranche is due for one account (`--account`, default `kis-isa`), place no orders |
| `rebalance_run.py` | Manual, interactive entry point; computes the plan for any account (`--account`) but only sends real orders for `toss-bot` today — see `RUNBOOK.md` |
| `tranche_init.py` | One-time: assign one account's existing holdings to tranches without trading |
| `backtest.py` | Historical simulation, including tranched/staggered-entry backtesting |
| `show.py` | Read-only views over recorded history |

**Tranching.** Capital is split into sleeves (`config.TRANCHES`, currently 3) that rebalance
on different trading days of the month, tracked in `tranche_holdings`. A sleeve missing its
scheduled day catches up at the next opportunity rather than skipping the month
(`tranche.due_today`) — except before `config.TRANCHE_START_MONTH`, when sleeves had no
schedule yet to miss. `tranche.reconcile` compares the summed sleeve books against actual
account holdings before every trade; a non-empty drift means something moved outside the
strategy (manual trade, dividend paid in shares, unrecorded fill) and the run must stop
rather than trade through it.

**Design decisions worth knowing before changing related code:**
- `TOSS_DRY_RUN`/`KIS_DRY_RUN` default to `true`. Until explicitly set `false` in `.env`,
  `create_order` logs the request and returns without sending it — this is the primary
  safety mechanism since there is no paper trading environment.
- `rebalance_run.py` used to additionally refuse real orders for any account other than
  `toss-bot`, regardless of `KIS_DRY_RUN` — a second, deliberate gate on `kis-isa` pending a
  live verification of `executor.execute()`'s retry loop against KIS. That verification
  passed and the guard was removed 2026-08-31; both accounts place real orders now. See
  `RUNBOOK.md`'s ISA go-live checklist for the record.
- Momentum lookups resolve to the last trading day at or before a target date, never by
  candle index — symbols don't share one trading calendar, and index-based lookup once
  silently shifted an entire ranking when the cache added one candle.
- Absolute momentum compares against a cash-proxy ETF (`config.CASH_SYMBOL`), not against
  zero — a small positive return isn't worth holding if cash yields more.
- Dividends are computed live, not estimated: Toss candles are price-only and would
  otherwise penalize high-yield holdings via the invisible ex-dividend drop, so
  `momentum.trailing_yield()` sums real KIS payout history (`quant/dividends.py`'s cache,
  fetched from KIS's 예탁원정보 배당일정 endpoint) over the trailing 12 months instead of
  reading a hand-maintained constant. **Sharp edge:** because this is now time-varying
  (unlike the old static `config.DIVIDEND_YIELD`), any code path calling
  `strategy.evaluate()`/`variants()` for a historical date — `backtest.py`, a `daily.py
  --date` backfill — must slice dividend events to that date first (`backtest.
  slice_dividends_at()`), exactly like candles are already sliced, or risk look-ahead bias.
- Orders are limit orders priced a tick or two through the touch, never market orders —
  a market order has no price ceiling and can fill far from the last trade during a thin or
  closing-auction book. `executor.MAX_DEVIATION` (2%) refuses to trade if the touch has
  moved too far from the last price.
- `storage.py` stores all `Decimal` values as text, never SQLite `REAL` — floats would
  reintroduce the precision error `Decimal` exists to avoid.
- The universe (`config.UNIVERSE`) was chosen for low mutual correlation, not backtested
  performance; excluded correlated symbols stay in `config.WATCH_ONLY` so their candles keep
  accumulating for later audit, but they are never traded.
- `strategy.variants()` computes several alternative allocations (different lookbacks,
  blended rank, trend filter, risk-parity/inverse-vol/signal/rank weighting) every run and
  records them via `storage.save_variants`, but only the primary `strategy.evaluate` signal
  is ever traded. Don't wire a variant into execution without an explicit decision to do so.
- **KIS API Quirks:** 
  - Field names (e.g., `tot_evlu_pfls_amt`) can mean entirely different things (Total Evaluation vs Total Profit/Loss) depending on the endpoint. Never trust a field name without mathematically verifying it against raw response data.
  - US Stock Tick Size: Measured as `$0.01` (returned as `e_hogau: 0.0100` via `HHDFS76200200`).
  - US Stock Exchange Codes: KIS API uses `NAS` (e.g., in REST queries) and `NASD` (in other contexts/Toss) differently; be precise with exchange codes.
  - Undocumented Fields: The `ordy` field in price endpoints (e.g., `HHDFS00000300` returning '매도불가') lacks clear enum documentation.
  - Overseas Orderbook: The 10-level overseas orderbook is available via `HHDFS76200100` (`orderbook_overseas` in `kis_client.py`), unlike the details endpoint `HHDFS76200200`.

## Working in this repo

- Live trading has no paper environment — treat any change to `executor.py`, `rebalance.py`,
  `rebalance_run.py`, or `tranche.py` as touching real money. Prefer adding tests over manual
  verification, and don't change order-sizing or price logic without matching test coverage.
- `RUNBOOK.md` documents the actual manual rebalance procedure (timing windows, dry-run
  unlock/lock sequence, reconciliation checklist) — read it before changing anything in that
  workflow's code path.
- The universe is frozen until November 2026 per `RUNBOOK.md`; don't suggest adding or
  removing symbols from `config.UNIVERSE` casually.
