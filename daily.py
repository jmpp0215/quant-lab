"""Daily signal run. Executed once per trading day by cron.

Fetches candles, evaluates the strategy, and records the ranking. Reruns
on the same trading date overwrite: the strategy only acts on completed
daily candles, so intraday recalculation carries no new information.

Placing orders is deliberately not part of this yet - the observation
period runs until the first scheduled rebalance.
"""

import json
import logging
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import backups
import price_history
from quant import (
    accounts,
    candles,
    config,
    dividends,
    indicators,
    kis_client,
    logging_config,
    market,
    schedule,
    storage,
    strategy,
    tranche,
)
from quant.kis_client import KisApiError, KisClient
from quant.notify import notify, ping
from quant.stock_factors.scripts import pbr_storage
from quant.toss_client import TossApiError
from rebalance_run import plan_for_tranche

log = logging.getLogger("daily")

# healthcheck.zsh (the launchd watchdog) stamps this file every run, weekdays
# 13:15 and 16:30. daily.py runs at 15:40, so a stamp older than 4 hours
# means today's 13:15 check never ran - the watchdog itself is down, and
# nothing else would say so.
HEALTHCHECK_HEARTBEAT = Path(__file__).parent / "logs" / "healthcheck.heartbeat"
HEALTHCHECK_MAX_AGE_SECS = 4 * 3600


def healthcheck_problem(path: Path, now: float,
                        max_age: float = HEALTHCHECK_MAX_AGE_SECS) -> str | None:
    """Why the watchdog looks dead, or None.

    No file at all is None, not a problem: that is the state before the
    watchdog is installed, and alerting on it daily would only train the
    user to ignore the alert.
    """
    try:
        stamp = float(path.read_text().strip())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return f"healthcheck heartbeat unreadable ({path})"
    age = now - stamp
    if age > max_age:
        return f"healthcheck heartbeat is {age / 3600:.1f}h old - the watchdog is not running"
    return None


def check_account_health(conn, account_name: str, snap: storage.AccountSnapshot,
                         trade_date: str) -> list[str]:
    """Read-only drift/cash checks against one account's fresh snapshot.

    Returns human-readable problem descriptions, empty when nothing is
    wrong. Never touches tranche books or places orders - this runs
    unattended every day, not just when a tranche happens to be due, so it
    must be safe to call regardless of what rebalance_run.py is doing.
    """
    problems = []

    books = storage.load_all_tranche_holdings(conn, account_name)
    if books:
        actual = {p["symbol"]: p["qty"] for p in snap.positions}
        drift = tranche.reconcile(books, actual)
        if drift:
            problems.append(f"tranche books disagree with the account: {drift}")

    # Cash changes that trades, distributions, the exchange rate and
    # settlement timing don't explain (quant/transfers.py) - reported only
    # once confirmed, i.e. not undone within the next few snapshots, and
    # until the user records or dismisses them in the tracker.
    suspects = [s for s in storage.transfer_suspects(conn, account_name)
                if s["kind"] == "transfer"]
    if suspects:
        problems.append("unrecorded transfer? " + ", ".join(
            f"{s['date']} {s['amount']:+,.0f} KRW" for s in suspects)
            + " - record or dismiss in the tracker")

    return problems


def accounts_in_slot(registry: dict, slot: str) -> list[str]:
    """Accounts whose daily snapshot belongs to this run - "close" (the
    15:40 run) or "morning" (`--morning`, before the open)."""
    return [name for name, cfg in registry.items()
            if cfg.get("snapshot_slot", "close") == slot]


def record_strategy(signal: strategy.Signal, trade_date: str,
                    session: str | None,
                    candles_by_symbol: dict[str, list[dict]],
                    dividend_events_by_symbol: dict[str, list[dict]]
                    ) -> None:
    """Record the shared dual-momentum signal - not account state."""
    now = datetime.now().astimezone().isoformat()
    rows = [
        {
            "symbol": score.symbol,
            "rank": rank,
            "momentum": score.momentum,
            "selected": score.symbol in signal.weights,
            "weight": signal.weights.get(score.symbol),
        }
        for rank, score in enumerate(signal.scores, 1)
    ]

    ind_rows = [
        (symbol, name, value)
        for symbol in config.all_symbols()
        for name, value in indicators.compute_all(
            candles_by_symbol.get(symbol, [])).items()
    ]

    variant_rows = [
        (v.name, symbol, weight)
        for v in strategy.variants(candles_by_symbol,
                                   dividend_events_by_symbol, signal)
        for symbol, weight in v.weights.items()
    ]

    with storage.connect() as conn:
        storage.save_run(conn, trade_date, now, session, ok=True)
        storage.save_scores(conn, trade_date, rows)
        storage.save_indicators(conn, trade_date, ind_rows)
        storage.save_variants(conn, trade_date, variant_rows)

    log.info("recorded %d scores, %d indicators, %d variant weights",
             len(rows), len(ind_rows), len(variant_rows))


# holidays() returns ~3 weeks from its base date, so three pages starting
# 20 days apart cover the next two months - enough for next month's
# tranches and the next PBR cycle.
CALENDAR_PAGE_OFFSETS = (0, 20, 40)
DIVIDEND_LOOKAHEAD_DAYS = 120
# Paid dividends are matched against cash changes (quant/transfers.py), so
# the schedule also reaches back - KR pay dates trail record dates by
# up to ~a month, and a symbol sold since may still pay.
DIVIDEND_LOOKBACK_DAYS = 60


def tranche_preview(weights: dict[str, Decimal], books: dict[int, dict[str, int]],
                    which: int, prices: dict[str, Decimal], cash: Decimal,
                    actual: dict[str, int]) -> dict:
    """What tranche `which` would trade at these prices - the sizing and
    plan rebalance_run.py shows before its first confirm, without placing
    anything. The live run reprices at the touch and resizes buys after
    its sells fill, so this is an estimate of that plan, not the plan.
    """
    book = books.get(which, {})
    budget = tranche.sleeve_budget(books, which, prices, cash)
    targets = tranche.target_quantities(weights, budget.value, prices)
    orders = plan_for_tranche(book, targets, prices)
    return {
        "tranche": config.TRANCHES.index(which) + 1,
        "tranches": len(config.TRANCHES),
        "drift": tranche.reconcile(books, actual) or None,
        "target": str(budget.target), "value": str(budget.value),
        "deposit_needed": str(budget.deposit_needed),
        "holds": str(tranche.sleeve_equity(book, prices)),
        "picks": [{"symbol": s, "name": config.UNIVERSE.get(s, s), "weight": str(w)}
                  for s, w in sorted(weights.items(), key=lambda kv: -kv[1])],
        "book": [{"symbol": s, "name": config.UNIVERSE.get(s, s), "qty": q}
                 for s, q in sorted(book.items())],
        "orders": [{"symbol": o.symbol, "name": o.name, "side": o.side,
                    "qty": o.quantity, "price": str(o.limit_price),
                    "notional": str(o.notional)} for o in orders],
    }


def record_upcoming(client: KisClient, past_days: list[str],
                    signal: strategy.Signal | None = None,
                    signal_date: str | None = None,
                    closes: dict[str, Decimal] | None = None) -> None:
    """Record upcoming rebalances and dividends for the asset tracker, and
    a preview of the next tranche's orders when the signal is at hand.

    Display only: the tranche and PBR dates project tranche.due_today's and
    check_pbr_due's rules forward, but those stay what decides a trade.
    Runs after the close, so scheduling starts from the next open day: a
    tranche or PBR run still due today can only happen then.
    """
    now = datetime.now(market.KST)
    today = now.strftime("%Y-%m-%d")
    future = schedule.open_days([
        client.holidays((now + timedelta(days=k)).strftime("%Y%m%d"))
        for k in CALENDAR_PAGE_OFFSETS])
    days = sorted(set(past_days) | set(future))
    upcoming_days = [d for d in days if d > today]
    start = upcoming_days[0] if upcoming_days else today

    strategy_account = accounts.DEFAULT_ACCOUNT
    with storage.connect() as conn:
        done = storage.tranches_done_this_month(conn, strategy_account, start)
        books = storage.load_all_tranche_holdings(conn, strategy_account)
        latest = conn.execute(
            "SELECT p.account, p.positions FROM portfolio p "
            "JOIN (SELECT account, MAX(trade_date) d FROM portfolio GROUP BY account) m "
            "ON p.account = m.account AND p.trade_date = m.d").fetchall()
        since = (now.date() - timedelta(days=DIVIDEND_LOOKBACK_DAYS)).isoformat()
        recent = conn.execute("SELECT positions FROM portfolio WHERE trade_date >= ?",
                              (since,)).fetchall()
    with pbr_storage.connect() as conn:
        pbr_full = pbr_storage.get_latest_full_rebalance_date(conn)

    rows = [{"kind": "tranche", "date": d, "account": strategy_account,
             "label": f"트랜치 {config.TRANCHES.index(t) + 1}/{len(config.TRANCHES)} "
                      "리밸런싱 (수동)"}
            for d, t in schedule.tranche_dates(days, start, done)]
    next_tranche = schedule.tranche_dates(days, start, done)[:1]

    if pbr_full is not None:
        due = schedule.trading_days_after(
            days, pbr_full, pbr_storage.FULL_REBALANCE_TRADING_DAYS)
        if due is not None and due < start:
            due = start   # overdue: check_pbr_due runs it next
        if due is not None:
            rows.append({"kind": "pbr", "date": due, "account": "toss-bot",
                         "label": "PBR 전체 리밸런싱 (자동)"})

    # Domestic holdings only: the KSD schedule doesn't cover overseas.
    holdings = [{"account": r["account"], "symbol": p["symbol"],
                 "name": p.get("name") or p["symbol"], "qty": p["qty"]}
                for r in latest for p in json.loads(r["positions"])
                if p.get("currency", "KRW") == "KRW"]
    symbols = {p["symbol"] for r in recent for p in json.loads(r["positions"])
               if p.get("currency", "KRW") == "KRW"}
    events = []
    until = now.date() + timedelta(days=DIVIDEND_LOOKAHEAD_DAYS)
    for symbol in sorted(symbols):
        try:
            fetched = kis_client.dividend_schedule(
                client, symbol, from_date=now.date() - timedelta(days=DIVIDEND_LOOKBACK_DAYS),
                to_date=until)
        except KisApiError as e:
            log.error("%s: dividend schedule failed: %s", symbol, e)
            continue
        with storage.connect() as conn:
            storage.save_dividend_schedule(conn, symbol, fetched, now.isoformat())
        events += [dict(e, symbol=symbol) for e in fetched]
    for d in schedule.upcoming_dividends(events, holdings, today):
        when = f" · 지급 {d['pay_date']}" if d["pay_date"] else ""
        per = f"주당 {d['per_share']:,}원" if d["per_share"] else "금액 미정"
        rows.append({"kind": "dividend", "date": d["record_date"],
                     "account": d["account"],
                     "label": f"{d['name']} 배당 기준일 · {d['qty']}주 · {per}{when}",
                     "amount": d["expected"]})

    preview = None
    if signal is not None and closes and next_tranche:
        due_date, which = next_tranche[0]
        cfg = accounts.ACCOUNTS[strategy_account]
        cash = cfg["buying_power"](client, closes)
        actual = {p["symbol"]: p["qty"] for r in latest
                  if r["account"] == strategy_account
                  for p in json.loads(r["positions"])}
        preview = tranche_preview(signal.weights, books, which, closes, cash, actual)
        preview |= {"date": due_date, "signal_date": signal_date, "cash": str(cash)}

    with storage.connect() as conn:
        storage.save_upcoming(conn, rows, now.isoformat())
        storage.save_preview(conn, strategy_account, preview, now.isoformat())
    log.info("upcoming: %d rows, preview: %s", len(rows),
             "none" if preview is None else f"{len(preview['orders'])} orders")


def main() -> int:
    logging_config.setup()
    # --morning snapshots only the "morning"-slot accounts (see
    # accounts.resolve): before the open, the newest completed candle is
    # the previous trading day's, so that is the date they are recorded
    # under - the same date the 15:40 run used for the other accounts.
    slot = "morning" if "--morning" in sys.argv else "close"
    log.info("daily run start (%s)", slot)

    # Before any early return (market closed, backfill): the watchdog can
    # die on a holiday too. Close run only: at 08:30 the last heartbeat is
    # yesterday's 16:30, which would always look stale.
    watchdog = (healthcheck_problem(HEALTHCHECK_HEARTBEAT, time.time())
                if slot == "close" else None)
    if watchdog:
        log.error(watchdog)
        notify("quant-lab", f"daily.py: {watchdog}")

    storage.init()
    # Recompute a past date from cached candles. The signal is a pure
    # function of the candles up to that date, so a missed run can be
    # filled in later without losing anything.
    target = None
    if "--date" in sys.argv:
        target = sys.argv[sys.argv.index("--date") + 1]
        log.info("backfilling %s", target)

    try:
        # Calendar and candles are shared market data, not account state.
        # Both come from KIS (holiday calendar + adjusted candles); this
        # client is reused for the kis-isa account below.
        market_data = KisClient("isa")

        if target is None:
            holiday = market_data.holidays(datetime.now(market.KST).strftime("%Y%m%d"))
            if not market.is_business_day(holiday):
                log.info("market closed today; nothing to record")
                if slot == "close":
                    ping()   # the dead man's switch expects a ping every weekday
                return 0
            session = market.current_session()
            session_closed = session in (None, "afterMarket")
        else:
            # A past date's candles are complete by definition.
            session = None
            session_closed = True

        # Once the regular session has closed, today's candle is final and
        # should drive the signal; before that it is still moving.
        data = {
            sym: candles.get(market_data, sym, days=config.HISTORY_DAYS,
                             include_today=session_closed, source="kis")
            for sym in config.all_symbols()
        }
        if target is not None:
            data = {
                sym: [c for c in cs if c["timestamp"][:10] <= target]
                for sym, cs in data.items()
            }

        # The trade date is the newest completed candle, not today - during
        # market hours these differ, and the signal belongs to the former.
        reference = data[next(iter(config.UNIVERSE))]
        if not reference:
            log.error("no candles at or before %s", target)
            return 1
        trade_date = reference[0]["timestamp"][:10]
        log.info("trade date: %s", trade_date)
    except Exception as e:
        log.exception("daily run failed")
        notify("quant-lab", f"daily.py 실패 (candle/calendar fetch): {e}")
        if slot == "close" and target is None:
            ping(ok=False)
        return 1

    overall_ok = True
    problem_accounts: list[str] = []
    strategy_signal = None
    # Reuse the market-data client for the kis-isa account so it does not
    # authenticate a second KIS session for the same credentials.
    client_cache: dict = {accounts.ACCOUNTS["kis-isa"]["client"]: market_data}
    for account_name in accounts_in_slot(accounts.ACCOUNTS, slot):
        cfg = accounts.ACCOUNTS[account_name]
        try:
            factory = cfg["client"]
            if factory not in client_cache:
                client_cache[factory] = factory()
            client = client_cache[factory]

            if cfg["strategy"]:
                if not isinstance(client, KisClient):
                    raise RuntimeError(
                        f"{account_name}: strategy account must be "
                        f"KIS-backed for live dividend sync, got "
                        f"{type(client).__name__}")
                with storage.connect() as conn:
                    dividends.sync_all(conn, client, config.all_symbols())
                    dividend_events = dividends.load_all(
                        conn, config.all_symbols())
                if target is not None:
                    # Same look-ahead guard the candle slice above already
                    # applies - a --date backfill must only see events
                    # known as of that date, not today's full cache.
                    dividend_events = {
                        s: [e for e in evs if e["record_date"] <= target]
                        for s, evs in dividend_events.items()
                    }
                signal = strategy.evaluate(data, dividend_events)
                strategy_signal = signal
                log.info("\n%s", strategy.format_signal(signal))
                record_strategy(signal, trade_date, session, data,
                               dividend_events)

            if target is None:
                # Holdings can only be observed now, never reconstructed,
                # so a backfilled date must not be given today's balance.
                snap = cfg["snapshot"](client)
                if snap.account != account_name:
                    log.warning("%s: snapshot labeled itself %s",
                               account_name, snap.account)
                with storage.connect() as conn:
                    storage.save_portfolio(conn, trade_date, account_name,
                                           snap.currency, snap.total,
                                           snap.cash, snap.positions)
                    problems = check_account_health(conn, account_name,
                                                     snap, trade_date)
                if problems:
                    msg = "; ".join(problems)
                    log.error("%s: %s", account_name, msg)
                    problem_accounts.append(f"[{account_name}] {msg}")
                    overall_ok = False
        except (TossApiError, KisApiError) as e:
            log.error("%s: api error: %s", account_name, e)
            problem_accounts.append(f"[{account_name}] api error: {e}")
            overall_ok = False
        except Exception as e:
            log.exception("%s: account run failed", account_name)
            problem_accounts.append(f"[{account_name}] {e}")
            overall_ok = False

    if slot == "close" and target is None:
        try:
            closes = {sym: Decimal(cs[0]["closePrice"]) for sym, cs in data.items() if cs}
            record_upcoming(market_data, [c["timestamp"][:10] for c in reference],
                            strategy_signal, trade_date, closes)
        except Exception as e:
            # The tracker's schedule is display only - its failure must not
            # fail the run, but should still be seen.
            log.exception("upcoming schedule failed")
            problem_accounts.append(f"[upcoming] {e}")

    if slot == "close" and target is None:
        # Realized overseas sells this year, for the tracker's capital gains
        # tax estimate. Overseas orders are placed by hand, not by this
        # system, so the broker's record is the only one.
        try:
            factory = accounts.ACCOUNTS["kis-main-overseas"]["client"]
            overseas = client_cache.get(factory) or factory()
            today = datetime.now(market.KST).date()
            sells = kis_client.overseas_realized(overseas, today.replace(month=1, day=1), today)
            with storage.connect() as conn:
                storage.save_overseas_realized(conn, "kis-main-overseas", today.year, sells)
        except Exception as e:
            log.exception("overseas realized fetch failed")
            problem_accounts.append(f"[overseas realized] {e}")
        try:
            # Display only (the tracker's crisis replay); after the trading
            # work so a slow or failing Yahoo fetch can't delay it.
            price_history.run(datetime.now(market.KST).date())
        except Exception as e:
            log.exception("price history fetch failed")
            problem_accounts.append(f"[price history] {e}")
        try:
            backups.run(datetime.now(market.KST).date().isoformat())
        except Exception as e:
            log.exception("backup failed")
            problem_accounts.append(f"[backup] {e}")

    if problem_accounts:
        notify("quant-lab", "daily.py: " + " / ".join(problem_accounts))
    if slot == "close" and target is None:
        ping(ok=overall_ok and not problem_accounts)

    # healthcheck.zsh greps today's log for "daily run done" to confirm the
    # 15:40 run happened; the morning run must not satisfy that check.
    log.info("daily run done" if slot == "close" else "morning snapshot done")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())