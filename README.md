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
| Value / quality stock factors — PBR, GPA | `quant/stock_factors/` | Live execution path exists (`scripts/run_pbr_live.py`), `DRY_RUN`-gated |
| Post-earnings announcement drift (PEAD) | `quant/pead/` | Research concluded, not traded — see `quant/pead/RESEARCH_LOG.md` |
| AMZN data-collection layer | `quant/amzn/` | Data collection only, no signal or execution yet — see `quant/amzn/README.md` |

The rest of this document covers dual momentum — the most developed track, and the only one
currently trading real money. The other tracks each carry their own README / research log.

---

## Dual momentum: the constraints that shaped it

Three properties of the Toss Open API drove most of the design.

**There is no paper trading environment.** Calling the order endpoint places a real order on a
real account. "Run it and see what breaks" is not available, so the safety mechanisms had to be
structural rather than procedural.

**IP allowlisting is managed only through the web UI.** If the egress IP changes, every request
returns 403 and stays broken until a human intervenes.

**Candles are capped at 100 per request and carry no dividend data.** A twelve-month lookback
requires pagination, and price-only returns systematically penalise high-yield holdings.

---

## Design decisions

### Safe by default

`TOSS_DRY_RUN` defaults to `true`. Until it is explicitly disabled, `create_order` logs the
request body and returns without sending it. Strategy logic can be exercised end to end without
touching the account, and a missing config file cannot cause an order.

### Momentum is looked up by date, not by index

`candles[252]` is only twelve months back if every symbol shares one trading calendar. It does
not — and a cache refresh that added a single candle once shifted the entire ranking. Lookups
now resolve to the last trading day at or before a target date.

### Dividends are approximated rather than ignored

The API returns price candles only, so the ex-dividend drop is visible while the payout is not.
The spread between KODEX 은행 (3.76%) and TIGER 나스닥100 (0.51%) is wide enough to invert
rankings. Distributing the annual yield across the holding window is imprecise, but far closer
than treating price return as total return.

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
| `quant/toss_client.py` | Toss REST client — token caching, 429 backoff, error envelope parsing |
| `quant/kis_client.py` | KIS REST client — overseas accounts, balances, and cash inquiries |
| `quant/candles.py` | Daily candle fetching, disk cache, exclusion of the in-progress candle |
| `quant/momentum.py` | Date-anchored lookback returns with dividend adjustment |
| `quant/strategy.py` | Dual momentum signal generation (pure) |
| `quant/rebalance.py` | Target weights → orders, with share rounding and tick-size snapping |
| `quant/tranche.py` | Capital splitting into staggered sleeves, drift reconciliation |
| `quant/executor.py` | Limit order placement, polling fills, deviation guards |
| `quant/storage.py` | SQLite persistence for signals, orders, portfolio snapshots, and cashflows |
| `quant/market.py` | Session detection, KRX tick sizes |
| `quant/config.py` | Universe and strategy parameters |
| `daily.py` | Entry point, driven by cron to compute and record daily signals |
| `check_due.py` | Entry point, driven by cron to notify if a tranche is due |
| `rebalance_run.py` | Manual, interactive entry point for actual trading |
| `tranche_init.py` | One-time entry point to assign existing holdings to tranches |
| `backtest.py` | Historical simulation and performance metrics |
| `show.py` | Read-only views over recorded history |
| `slippage.py` | Execution slippage analysis (limit vs avg fill price) |
| `tests/` | Unit tests for strategy, allocation, executor, and tranche logic |

`quant.strategy.evaluate` takes candles and returns target weights. It has no access to the network,
the clock, or the filesystem, so live trading and backtesting can share the same code path.

The other research tracks sit in their own packages beside these core modules —
`quant/stock_factors/`, `quant/pead/`, `quant/amzn/`, `quant/common/` — each with its own
modules, tests, and docs.

---

## Running it

```bash
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in credentials

python check_setup.py       # read-only connectivity check
python daily.py             # compute and record today's signal
```

State and signals are stored locally in a SQLite database at `data/quant.db`.

---

## Status

- [x] API client with retry and rate-limit handling (Toss, KIS)
- [x] Candle collection and local cache
- [x] Dual momentum signal generation
- [x] Order planning from target weights
- [x] Scheduled daily runs
- [x] Order execution (sell → confirm fills → buy)
  - Toss: Live trading verified
  - KIS: Domestic live trading verified, overseas pending live API test
- [x] Backtest and performance metrics
- [ ] Failure notifications