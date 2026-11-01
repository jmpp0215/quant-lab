# quant-lab

Personal quant research project built on the Toss Securities and KIS (한국투자증권) Open APIs.
A place to prototype systematic strategies against real Korea/US market data and trade the
survivors live on a personal brokerage account.

> Personal research project. Not investment advice. Use at your own risk.

---

## Research tracks

| Track | Location | Status |
|---|---|---|
| Dual momentum — eight Korea-listed ETFs, monthly staggered rebalance | repo root + `quant/` | **Live** on `kis-isa` (see `RUNBOOK.md`) |
| Asset tracker — read-only PWA over every account, plus display-only research side jobs | `tracker_server.py`, `tracker_web/`, `quant/tracker.py` | Running daily; never places orders |
| Value / quality stock factors — PBR, GPA | `quant/stock_factors/` | Live execution path exists (`scripts/run_pbr_live.py`), `DRY_RUN`-gated |
| Post-earnings announcement drift (PEAD) | `quant/pead/` | Research concluded, not traded — see `quant/pead/RESEARCH_LOG.md` |
| AMZN data collection + accumulation timing | `quant/amzn/` | Notify-only daily check, no execution; timing backtest found no rule beats buying on the month's first day — see `quant/amzn/README.md` |

The rest of this document covers dual momentum — the most developed track. The other tracks
each carry their own README / research log; the research history across tracks is in
`quant/pead/RESEARCH_LOG.md`, where every test is pre-registered before it runs.

### Accounts

Trading is multi-account (`quant/accounts.py`), selected with `--account`:

| Account | Broker | Role |
|---|---|---|
| `kis-isa` | KIS | Primary dual momentum account; the default for every script |
| `toss-bot` | Toss | Sandbox for execution-path trials, off the tranche schedule |
| `kis-main`, `kis-main-overseas` | KIS | Snapshotted daily for the tracker, never traded by the strategy |

---

## Dual momentum: the constraints that shaped it

Three properties of the broker APIs drove most of the design.

**There is no paper trading environment.** Calling the order endpoint places a real order on a
real account. "Run it and see what breaks" is not available, so the safety mechanisms had to be
structural rather than procedural.

**Toss IP allowlisting is managed only through the web UI.** If the egress IP changes, every request
returns 403 and stays broken until a human intervenes.

**Candles are capped at 100 per request.** A twelve-month lookback requires pagination. The
candles are adjusted prices (수정주가), so distributions are already in the series.

---

## Design decisions

### Safe by default

`TOSS_DRY_RUN` and `KIS_DRY_RUN` default to `true`. Until one is explicitly disabled,
`create_order` logs the request body and returns without sending it. Strategy logic can be
exercised end to end without touching the account, and a missing config file cannot cause an
order.

### Reorder only from a confirmed final state

Orders are limit orders a tick or two through the touch, never market orders, and the executor
refuses to trade when the touch is more than 2% from the last price. Each order is tracked as an
explicit state (open, partially filled, cancel pending, canceled, filled, unknown). The
remainder is re-sent only after the broker confirms a final state with its filled quantity. If
that state can't be read, the run stops with `ExecutionHalted`, records only the confirmed
fills, and leaves the difference for the next run's drift check, instead of guessing and
possibly buying twice.

### Sleeves are reconciled before every trade

Capital is split into three tranches that rebalance on different trading days of the month.
Before any order, the summed sleeve books are compared against actual holdings. Any drift (a
manual trade, a dividend paid in shares, an unrecorded fill) stops the run.

### Momentum is looked up by date, not by index

`candles[252]` is only twelve months back if every symbol shares one trading calendar. It does
not — and a cache refresh that added a single candle once shifted the entire ranking. Lookups
now resolve to the last trading day at or before a target date.

### No dividend term on top of adjusted prices

An earlier version added each symbol's trailing yield to its price return, on the assumption
that candles showed the ex-dividend drop but not the payout. A live check in September 2026
showed both Toss and KIS candles are already adjusted: a money-market ETF showed no drop across
15 distributions. The yield term was counting distributions twice, inflating high-yield names by
3–5pp and flipping the top three on some days. It was removed from both the signal and the
backtest. Distribution events are still synced, for reporting only.

### Absolute momentum uses cash as the hurdle

A symbol must beat the CD-rate ETF, not merely beat zero. A 2% gain is not worth holding when
risk-free cash returns 2.7%.

### The universe was selected on correlation, not performance

KODEX AI전력핵심설비 returned +113% over twelve months and was still excluded: it ran 0.75
correlated with 코스피200 and 0.68 with 조선, so holding both added little diversification.
Excluded symbols stay in `WATCH_ONLY` so their candles keep accumulating and the decision can
be audited later against the signal log.

---

## Layout

The codebase separates core logic (`quant/`) from executable entry points (project root).

| Module | Responsibility |
|---|---|
| `quant/accounts.py` | Account registry and `--account` parsing |
| `quant/broker.py` | Execution interface every broker client implements (dataclasses only) |
| `quant/toss_client.py` | Toss REST client — token caching, 429 backoff, error envelope parsing |
| `quant/kis_client.py` | KIS REST client — domestic and overseas endpoints, per-account credentials |
| `quant/candles.py` | Daily candles from Toss or KIS, per-source disk cache, in-progress candle excluded |
| `quant/dividends.py` | Distribution events from KIS, cached for reporting (not on the signal path) |
| `quant/momentum.py` | Date-anchored lookback returns on adjusted prices (pure) |
| `quant/indicators.py`, `quant/allocation.py` | Trend filter; risk-parity / inverse-vol weighting for recorded variants |
| `quant/strategy.py` | Dual momentum signal generation (pure), plus recorded-but-untraded variants |
| `quant/rebalance.py` | Target weights → orders, with share rounding and tick-size snapping |
| `quant/tranche.py` | Capital splitting into staggered sleeves, drift reconciliation |
| `quant/executor.py` | Limit orders at the touch, order-state tracking, confirmed-state retries, deviation/auction guards |
| `quant/returns.py` | Principal-based and time-weighted account returns (pure) |
| `quant/storage.py` | SQLite persistence for signals, orders, portfolio snapshots, tranche books, and cashflows |
| `quant/market.py` | Session detection (KIS holiday calendar + KST clock), KRX tick sizes |
| `quant/notify.py` | Desktop and iMessage notifications, dead man's switch ping |
| `quant/config.py` | Universe and strategy parameters |
| `daily.py` | Entry point, driven by launchd: records the signal, snapshots every account, runs the side jobs; never trades |
| `check_due.py` | Entry point, driven by launchd to notify if a tranche is due |
| `rebalance_run.py` | Manual, interactive entry point for actual trading |
| `tranche_init.py` | One-time entry point to assign existing holdings to tranches |
| `backtest.py` | Historical simulation and performance metrics |
| `show.py` | Read-only views over recorded history |
| `slippage.py` | Execution slippage analysis (limit vs avg fill price) |
| `tracker_server.py`, `tracker_web/` | Asset tracker: localhost HTTP server + PWA over a read-only DB connection |
| `tests/` | Unit tests; each research package also has its own `tests/` |

`quant.strategy.evaluate` takes candles and returns target weights. It has no access to the network,
the clock, or the filesystem, so live trading and backtesting can share the same code path.

The other research tracks sit in their own packages beside these core modules —
`quant/stock_factors/`, `quant/pead/`, `quant/amzn/`, `quant/common/` — each with its own
modules, tests, and docs.

The asset tracker's pure logic (returns, FX split, exposure, risk grade, stress replay,
sentiment, market regime warning) lives in `quant/tracker.py`, `quant/stress.py`,
`quant/sentiment.py`, and `quant/regime.py`. Its side jobs (price history, news risk, regime
check, overnight US market recap) are display-only. A failure in one is reported to the phone
and never stops the trading part of `daily.py`. LLM features run through the Claude Code CLI
(`claude -p`) and send names and share counts only, never amounts.

---

## Running it

```bash
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in credentials

python check_setup.py       # read-only connectivity check
python daily.py             # compute and record today's signal (no orders)
python check_due.py         # is a tranche due today?
python rebalance_run.py     # interactive: place the orders for today's tranche
pytest                      # full test suite
```

Read `RUNBOOK.md` before running `rebalance_run.py` with dry run off.

State and signals are stored locally in a SQLite database at `data/quant.db`.

---

## Status

- [x] API client with retry and rate-limit handling (Toss, KIS)
- [x] Candle collection and local cache
- [x] Dual momentum signal generation
- [x] Order planning from target weights
- [x] Scheduled daily runs
- [x] Order execution (sell → confirm fills → buy)
  - Toss and KIS domestic: live trading verified (KIS on 2026-08-31)
  - Order-state executor (October 2026): KIS status fields checked against live order history; Toss pending
  - KIS overseas: read-only (snapshots, quotes); no overseas orders
- [x] Multi-account support and tranche drift reconciliation
- [x] Backtest and performance metrics (Sharpe on excess return over the cash proxy)
- [x] Failure notifications (desktop + iMessage)
- [x] Asset tracker PWA