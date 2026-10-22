"""Asset tracker: serves tracker_web/ and its data as one JSON document.

    python tracker_server.py                 http://127.0.0.1:8765
    python tracker_server.py --port 9000

Reads with mode=ro; the writes are POST /cashflow (an external
deposit/withdrawal, the same row cashflow.py records), POST
/transfer/dismiss (a suspected transfer the user says wasn't one) and
POST /settings/isa (ISA details). Nothing on the
order path (or any broker client) is imported. Bound to localhost only -
phone access goes through `tailscale serve`, which adds HTTPS (needed for
the PWA to install) without exposing the port to the internet.

All the data is one document, /data.json, rebuilt per request. Keeping it
one static-shaped file means the page could later be served from a
static host with a daily-uploaded data.json and no code change.
"""

import json
import logging
import sqlite3
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from quant import config, returns, storage, tracker, transfers
from quant.stock_factors.scripts import pbr_slippage, pbr_storage

log = logging.getLogger("tracker")

WEB_DIR = Path(__file__).parent / "tracker_web"
DEFAULT_PORT = 8765
KST = ZoneInfo("Asia/Seoul")

# Goal: None hides the projection. Monthly contribution and assumed return
# are planning inputs, not measured figures.
GOAL_TARGET = None           # KRW, e.g. Decimal("100000000")
GOAL_MONTHLY = Decimal("0")  # KRW added per month
GOAL_ANNUAL_RETURN = Decimal("0.05")

# ISA details the user enters in the tracker (settings table): limits are
# not hard-coded because the 2026 reform (reported: 40M/yr, 200M total,
# tax-free 10M for 서민형) wasn't confirmed against an official source on
# 2026-10-23, and paid-in can't come from cashflows - the "principal" row is
# a cost basis on the tracker start date, not what was paid in.
ISA_SETTINGS = {
    "isa_type": "text", "isa_open_date": "date", "isa_paid_total": "won",
    "isa_paid_this_year": "won", "isa_annual_limit": "won",
    "isa_total_limit": "won", "isa_taxfree_limit": "won",
}
ISA_MANDATORY_YEARS = 3        # 의무가입기간

# What each account's principal row stands for (cashflows kind
# "principal"), shown under the principal return. Anything not listed is
# the 10/21 cost basis.
PRINCIPAL_BASIS = {"kis-isa": "납입원금", "toss-bot": "8/13 잔액+이후 입금"}
DEFAULT_PRINCIPAL_BASIS = "매입금액+현금"
ISA_EXCESS_TAX = Decimal("0.099")   # 분리과세 on gains over the tax-free limit

# Compared against on the home chart and in each account's detail, from
# the KIS candle cache daily.py keeps (KRW-listed, so FX is included the
# same way it is in the accounts).
BENCHMARKS = [("102110", "코스피200"), ("133690", "나스닥100"),
              (config.CASH_SYMBOL, "현금성(CD·MMF)")]
CANDLE_DIR = storage.DB_PATH.parent / "candles" / "kis"

# Backtest return bands (scratch/pbr_fan.py, scratch/momproxy/fan.py) and the
# date each account's live strategy started, to overlay the account on them.
FAN_DIR = storage.DB_PATH.parent / "backtest_fan"
FANS = {"kis-isa": ("momentum", "2026-08-28"),   # momentum principal date
        "toss-bot": ("pbr", "2026-09-04")}       # first full PBR rebalance

# The `prediction` skill's journal, read only.
JOURNAL_DIR = Path(__file__).parent / "journal" / "predictions"

# The upcoming list only looks this far ahead.
UPCOMING_DAYS = 30

# Rate decisions, by the KST date the decision lands. Hand-maintained:
# extend when each year's calendar is published.
# FOMC: federalreserve.gov/monetarypolicy/fomccalendars.htm (read
#   2026-10-23); the statement comes at 2pm ET on the meeting's second day,
#   i.e. 03:00 KST next day (EDT) or 04:00 (EST).
# BOK: bok.or.kr 통화정책방향 결정회의 일정, matching tossbank.com/articles/
#   baserate2601 (read 2026-10-23).
MACRO_EVENTS = [
    ("2026-10-29", "FOMC 금리 결정 (03:00)"),
    ("2026-11-26", "한국은행 기준금리 결정"),
    ("2026-12-10", "FOMC 금리 결정 (04:00, 경제전망 포함)"),
]

ISA_ACCOUNT = "kis-isa"   # tax-free dividends, ISA limits
CASHFLOW_ACCOUNTS = ("kis-isa", "kis-main", "kis-main-overseas", "toss-bot")
LABELS = {"kis-isa": "ISA (KIS)", "kis-main": "KIS 국내",
          "kis-main-overseas": "KIS 해외", "toss-bot": "토스"}


def _num(d: Decimal | None) -> float | None:
    return None if d is None else float(d)


def _fan(account: str, own_index: list[tuple[str, Decimal]]) -> dict | None:
    """The account's backtest band plus its own return since the strategy
    started, by trading days elapsed (the KIS candle cache's calendar)."""
    if account not in FANS:
        return None
    name, start = FANS[account]
    path = FAN_DIR / f"{name}.json"
    calendar_path = CANDLE_DIR / f"{BENCHMARKS[0][0]}.json"
    if not path.exists() or not calendar_path.exists():
        return None
    fan = json.loads(path.read_text())
    calendar = sorted(c["timestamp"][:10] for c in json.loads(calendar_path.read_text()))
    base = next((v for d, v in own_index if d >= start), None)
    if base is None or start not in calendar:
        return None
    k0 = calendar.index(start)
    actual = [{"k": calendar.index(d) - k0, "date": d, "ret": _num(v / base - 1)}
              for d, v in own_index if d >= start and d in calendar]
    last = actual[-1]
    return fan | {"start": start, "actual": actual,
                  "percentile": tracker.fan_percentile(fan, last["k"], last["ret"])}


def _benchmarks(dates: list[str]) -> list[dict]:
    out = []
    for symbol, label in BENCHMARKS:
        path = CANDLE_DIR / f"{symbol}.json"
        candles = json.loads(path.read_text()) if path.exists() else []
        out.append({"symbol": symbol, "label": label,
                    "returns": [_num(r) for r in tracker.benchmark_series(candles, dates)]})
    return out


def _pbr_fills(conn: sqlite3.Connection, names: dict[str, str]) -> list[dict]:
    """Per PBR rebalance: fill outcome and slippage (pbr_slippage.measure,
    the same figures `python -m ...pbr_slippage` prints), newest first."""
    pbr = sqlite3.connect(f"file:{pbr_storage.DB_PATH}?mode=ro", uri=True)
    try:
        rebalances = pbr.execute(
            "SELECT trade_date, summary_json FROM pbr_rebalance "
            "ORDER BY trade_date DESC").fetchall()
        out = []
        for d, summary in rebalances:
            orders = pbr.execute(
                "SELECT symbol, side, qty, limit_price, filled_qty, avg_fill_price "
                "FROM pbr_orders WHERE trade_date = ?", (d,)).fetchall()
            if not orders:
                continue
            closes = {sym: Decimal(str(c)) for sym, c in conn.execute(
                "SELECT symbol, close FROM pead_price_raw WHERE date = ? "
                "AND close IS NOT NULL", (d.replace("-", ""),))}
            m = pbr_slippage.measure(orders, closes)
            summary = json.loads(summary) if summary else {}
            out.append({
                "date": d, "retry": bool(summary.get("retry_of")),
                "orders": len(orders), "filled": m["filled"],
                "partial": m["partial"], "unfilled": m["unfilled"],
                "missed": [names.get(r[1], r[1]) for r in m["rows"]
                           if r[8] != "filled"],
                "vs_limit": _num(m["vs_limit_krw"] / m["notional"])
                if m["notional"] else None,
                "vs_close": _num(m["vs_close_krw"] / m["close_notional"])
                if m["close_notional"] else None,
                "vs_close_krw": _num(m["vs_close_krw"]),
            })
        return out
    finally:
        pbr.close()


def _dividends(conn: sqlite3.Connection, snaps: list, names: list[str],
               suspects_by_account: dict[str, list[dict]]) -> dict:
    """Announced dividends per account (dividend_schedule x shares held),
    each marked received when quant/transfers.py saw a dividend-sized
    cash rise in the step containing its pay date."""
    events = storage.load_dividend_schedule(conn)
    rows = []
    for a in names:
        a_snaps = [{"date": s_["trade_date"], "positions": json.loads(s_["positions"])}
                   for s_ in snaps if s_["account"] == a]
        seen = [(c["prev_date"], c["date"]) for c in suspects_by_account.get(a, [])
                if c["kind"] == "dividend"]
        for r in transfers.dividend_payouts(a_snaps, events, tax_free=a == ISA_ACCOUNT):
            pay = r["pay_date"]
            rows.append({
                "account": a, "label": LABELS.get(a, a), "name": r["name"],
                "record_date": r["record_date"], "pay_date": pay,
                "per_share": _num(r["per_share"]), "qty": r["qty"],
                "gross": _num(r["gross"]), "net": _num(r["net"]),
                "received": bool(pay) and any(p < pay <= d for p, d in seen),
            })
    return {"coverage_from": min((e["record_date"] for e in events), default=None),
            "rows": rows}


def _contributions(snaps: list, names: list[str], flows: list,
                   suspects_by_account: dict[str, list[dict]], today: date) -> dict:
    """tracker.contributions for this month, last month and all history."""
    by_account = {a: [{"date": s_["trade_date"], "total": Decimal(s_["total"]),
                       "cash": Decimal(s_["cash"]), "positions": json.loads(s_["positions"])}
                      for s_ in snaps if s_["account"] == a] for a in names}
    deposits = {a: [(f["trade_date"], Decimal(f["amount"])) for f in flows
                    if f["account"] == a and f["kind"] == "deposit"] for a in names}
    dividends = {a: [(c["date"], c["amount"]) for c in suspects_by_account[a]
                     if c["kind"] == "dividend"] for a in names}
    first = today.replace(day=1)
    last_month_end = first - timedelta(days=1)
    periods = {"this_month": (first.isoformat(), today.isoformat()),
               "last_month": (last_month_end.replace(day=1).isoformat(),
                              last_month_end.isoformat()),
               "all": ("0000-00-00", today.isoformat())}
    out = {}
    for key, (start, end) in periods.items():
        rows = tracker.contributions(by_account, deposits, dividends, start, end)
        out[key] = {"start": start, "end": end,
                    "rows": [{"account": r["account"], "label": LABELS.get(r["account"], r["account"]),
                              "name": r["name"], "kind": r["kind"],
                              "amount": _num(r["amount"])} for r in rows]}
    return out


def build(conn: sqlite3.Connection, today: date) -> dict:
    snaps = conn.execute(
        "SELECT account, trade_date, total, cash, positions FROM portfolio "
        "ORDER BY trade_date").fetchall()
    flows = conn.execute(
        "SELECT account, trade_date, amount, kind FROM cashflows").fetchall()

    names = list(dict.fromkeys(s["account"] for s in snaps))
    by_account = {a: [(s["trade_date"], Decimal(s["total"])) for s in snaps
                      if s["account"] == a] for a in names}
    deposits = {a: [(f["trade_date"], Decimal(f["amount"])) for f in flows
                    if f["account"] == a and f["kind"] == "deposit"]
                for a in names}
    latest = {s["account"]: s for s in snaps}   # rows are date-ordered

    # Symbol -> name from every snapshot: PBR orders store codes only, and a
    # symbol sold since is no longer in the latest positions.
    names_seen = {p["symbol"]: p.get("name") or p["symbol"]
                  for s_ in snaps for p in json.loads(s_["positions"])}

    account_rows = []
    for a in names:
        r = returns.compute(by_account[a], [
            (f["trade_date"], Decimal(f["amount"]), f["kind"]) for f in flows
            if f["account"] == a])
        series = by_account[a]
        prev = series[-2][1] if len(series) > 1 else None
        own = tracker.growth_index({a: series}, {a: deposits[a]})
        row = latest[a]
        total_a = Decimal(row["total"])
        held_a = tracker.holdings([{"total": total_a, "cash": Decimal(row["cash"]),
                                    "positions": json.loads(row["positions"])}])
        fx = None
        rate = tracker.implied_rate({"total": total_a, "cash": Decimal(row["cash"]),
                                     "positions": json.loads(row["positions"])})
        split = tracker.fx_split(json.loads(row["positions"]), rate) if rate else []
        if split:
            cost = sum((x.cost for x in split), Decimal("0"))
            cost_krw = sum((x.cost_krw for x in split), Decimal("0"))
            rates = []
            for snap in snaps:
                if snap["account"] != a:
                    continue
                r_ = tracker.implied_rate({"total": Decimal(snap["total"]),
                                           "cash": Decimal(snap["cash"]),
                                           "positions": json.loads(snap["positions"])})
                if r_ is not None:
                    rates.append({"date": snap["trade_date"], "rate": _num(r_)})
            fx = {
                "rate": _num(rate), "buy_rate": _num(cost_krw / cost),
                "cost": _num(cost), "cost_krw": _num(cost_krw),
                "value": _num(sum((x.value for x in split), Decimal("0"))),
                "value_krw": _num(sum((x.value_krw for x in split), Decimal("0"))),
                "price_effect": _num(sum((x.price_effect for x in split), Decimal("0"))),
                "fx_effect": _num(sum((x.fx_effect for x in split), Decimal("0"))),
                "holdings": [{"name": x.name, "usd_return": _num(x.value / x.cost - 1),
                              "krw_return": _num(x.value_krw / x.cost_krw - 1),
                              "buy_rate": _num(x.buy_rate),
                              "price_effect": _num(x.price_effect),
                              "fx_effect": _num(x.fx_effect)}
                             for x in sorted(split, key=lambda x: -x.value_krw)],
                "rate_history": rates,
            }
        account_rows.append({
            "account": a, "label": LABELS.get(a, a),
            "value": _num(series[-1][1]), "as_of": series[-1][0],
            "day_change": _num(series[-1][1] - prev) if prev is not None else None,
            "since": r.start, "money_in": _num(r.money_in),
            "principal_basis": PRINCIPAL_BASIS.get(a, DEFAULT_PRINCIPAL_BASIS),
            "principal_return": _num(r.principal_return),
            "time_weighted": _num(r.time_weighted),
            "history": [{"date": d, "total": _num(v), "index": _num(i)}
                        for (d, v), (_, i) in zip(series, own, strict=True)],
            "holdings": [{"name": n, "weight": _num(v / total_a)}
                         for n, v in held_a] if total_a else [],
            "fx": fx,
            "benchmarks": _benchmarks([d for d, _ in series]),
            "fan": _fan(a, own),
            "pbr": _pbr_fills(conn, names_seen) if a == "toss-bot" else None,
        })

    index = tracker.growth_index(by_account, deposits)
    dates = [d for d, _ in index]
    totals = []
    for d in dates:   # combined value, each account carried forward
        totals.append(sum((next((v for sd, v in reversed(s) if sd <= d),
                                Decimal("0")) for s in by_account.values()),
                          Decimal("0")))
    total = totals[-1] if totals else Decimal("0")

    held = tracker.holdings([
        {"total": Decimal(r["total"]), "cash": Decimal(r["cash"]),
         "positions": json.loads(r["positions"])} for r in latest.values()])
    weights = [(name, v / total) for name, v in held] if total else []

    goal = None
    if GOAL_TARGET is not None:
        months = tracker.months_to_goal(total, GOAL_TARGET, GOAL_MONTHLY,
                                        GOAL_ANNUAL_RETURN)
        goal = {"target": _num(GOAL_TARGET), "monthly": _num(GOAL_MONTHLY),
                "annual_return": _num(GOAL_ANNUAL_RETURN),
                "progress": _num(total / GOAL_TARGET), "months": months,
                "eta": tracker.month_after(today, months)
                if months is not None else None}

    settings = storage.load_settings(conn)
    isa = {k: settings.get(k) for k in ISA_SETTINGS}
    # Paid-in as entered, plus ISA deposits recorded after it was entered,
    # so a contribution is recorded once (as a cashflow) and counted here too.
    row = conn.execute("SELECT updated_at FROM settings WHERE key = 'isa_paid_total'").fetchone()
    if row and isa["isa_paid_total"]:
        as_of = row["updated_at"][:10]
        paid = tracker.isa_paid(
            Decimal(isa["isa_paid_total"]),
            Decimal(isa["isa_paid_this_year"]) if isa["isa_paid_this_year"] else None,
            as_of, [(f["trade_date"], Decimal(f["amount"])) for f in flows
                    if f["account"] == ISA_ACCOUNT and f["kind"] == "deposit"], today)
        isa["isa_paid_total"] = str(paid["total"])
        isa["isa_paid_this_year"] = None if paid["this_year"] is None else str(paid["this_year"])
        isa["paid_as_of"], isa["paid_added"] = as_of, _num(paid["added"])
    won_ = {k: Decimal(isa[k]) for k, kind in ISA_SETTINGS.items() if kind == "won" and isa[k]}
    isa_value = Decimal(latest[ISA_ACCOUNT]["total"]) if ISA_ACCOUNT in latest else None
    if isa["isa_open_date"]:
        opened = date.fromisoformat(isa["isa_open_date"])
        isa["mandatory_until"] = opened.replace(
            year=opened.year + ISA_MANDATORY_YEARS).isoformat()
    if isa_value is not None and "isa_paid_total" in won_:
        gain = isa_value - won_["isa_paid_total"]
        isa["value"], isa["gain"] = _num(isa_value), _num(gain)
        if "isa_taxfree_limit" in won_:
            isa["excess_tax"] = _num(max(gain - won_["isa_taxfree_limit"], Decimal("0"))
                                     * ISA_EXCESS_TAX)

    sells = [dict(r) for r in conn.execute(
        "SELECT * FROM overseas_realized WHERE date LIKE ? ORDER BY date",
        (f"{today.year}-%",))]
    gains = tracker.overseas_gains_tax(sells)
    overseas_tax = {k: _num(v) for k, v in gains.items()} | {
        "year": today.year,
        "sells": [{"date": r["date"], "name": r["name"], "qty": r["qty"],
                   "pnl_usd": _num(Decimal(r["pnl"])),
                   "pnl_krw": _num(Decimal(r["pnl"]) * Decimal(r["rate"]))} for r in sells],
        # Held at a KRW loss: selling would offset gains (judgement is the user's).
        "losers": [{"name": h["name"], "loss": h["price_effect"] + h["fx_effect"],
                    "krw_return": h["krw_return"]}
                   for a in account_rows if a["fx"] for h in a["fx"]["holdings"]
                   if h["price_effect"] + h["fx_effect"] < 0],
    }

    rebalances = [{"date": r["d"], "account": "kis-isa", "label": "ISA 트랜치"}
                  for r in conn.execute(
                      "SELECT DISTINCT executed_date d FROM orders "
                      "WHERE account = 'kis-isa' AND filled_qty > 0 "
                      "AND executed_date IS NOT NULL")]
    pbr = sqlite3.connect(f"file:{pbr_storage.DB_PATH}?mode=ro", uri=True)
    try:
        for (d, summary) in pbr.execute(
                "SELECT trade_date, summary_json FROM pbr_rebalance"):
            retry = summary and json.loads(summary).get("retry_of")
            rebalances.append({"date": d, "account": "toss-bot",
                               "label": "PBR 재시도" if retry else "PBR 리밸런싱"})
    finally:
        pbr.close()
    until = (today + timedelta(days=UPCOMING_DAYS)).isoformat()
    upcoming = [{"kind": r["kind"], "date": r["date"], "account": r["account"],
                 "label": r["label"],
                 "amount": _num(Decimal(r["amount"])) if r["amount"] else None}
                for r in conn.execute(
                    "SELECT * FROM upcoming WHERE date >= ? AND date <= ?",
                    (today.isoformat(), until))]
    upcoming += [{"kind": "macro", "date": d, "account": None, "label": label,
                  "amount": None}
                 for d, label in MACRO_EVENTS if today.isoformat() <= d <= until]
    predictions = [tracker.parse_prediction(f.read_text(encoding="utf-8")) | {"file": f.name}
                   for f in sorted(JOURNAL_DIR.glob("*.md"))] if JOURNAL_DIR.exists() else []
    upcoming += [{"kind": "prediction", "date": e["resolve_by"], "account": None,
                  "label": f"예측 판정: {e['title']}", "amount": None}
                 for e in predictions if e.get("status") == "open" and e.get("resolve_by")
                 and today.isoformat() <= e["resolve_by"] <= until]
    upcoming.sort(key=lambda u: u["date"])
    row = conn.execute("SELECT payload, computed_at FROM rebalance_preview "
                       "WHERE account = 'kis-isa'").fetchone()
    preview = None
    if row is not None:
        preview = json.loads(row["payload"]) | {"computed_at": row["computed_at"]}

    suspects_by_account = {a: storage.transfer_suspects(conn, a) for a in names}
    suspects = [{"account": c["account"], "label": LABELS.get(c["account"], c["account"]),
                 "date": c["date"], "prev_date": c.get("prev_date"),
                 "amount": _num(c["amount"]), "pending": c["kind"] == "pending"}
                for a in names for c in suspects_by_account[a]
                if c["kind"] in ("transfer", "pending")]

    recent_flows = [{"date": f["trade_date"], "account": f["account"],
                     "label": LABELS.get(f["account"], f["account"]),
                     "amount": _num(Decimal(f["amount"])), "note": f["note"]}
                    for f in conn.execute(
                        "SELECT trade_date, account, amount, note FROM cashflows "
                        "WHERE kind = 'deposit' ORDER BY trade_date DESC, id DESC "
                        "LIMIT 10")]

    return {
        "generated": today.isoformat(),
        "as_of": dates[-1] if dates else None,
        "total": _num(total),
        "day_change": _num(totals[-1] - totals[-2]) if len(totals) > 1 else None,
        "time_weighted": _num(index[-1][1] - 1) if index else None,
        "accounts": account_rows,
        "history": [{"date": d, "total": _num(t), "index": _num(i)}
                    for (d, i), t in zip(index, totals, strict=True)],
        "benchmarks": _benchmarks(dates),
        "risk": {
            "days": len(index),
            "max_drawdown": _num(tracker.max_drawdown(index)),
            "drawdown": [{"date": d, "dd": _num(v)}
                         for d, v in tracker.drawdown_series(index)],
            "volatility": _num(tracker.volatility(index)),
            "hhi": _num(tracker.hhi([w for _, w in weights])) if weights else None,
            "holdings": [{"name": n, "weight": _num(w)} for n, w in weights],
        },
        "goal": goal,
        "isa": isa,
        "overseas_tax": overseas_tax,
        "predictions": {"entries": predictions,
                        "stats": tracker.prediction_stats(predictions)},
        "rebalances": sorted(rebalances, key=lambda r: r["date"]),
        "upcoming": upcoming,
        "preview": preview,
        "suspects": suspects,
        "dividends": _dividends(conn, snaps, names, suspects_by_account),
        "contributions": _contributions(snaps, names, flows, suspects_by_account, today),
        "cashflows": recent_flows,
        "cashflow_accounts": [{"account": a, "label": LABELS[a]}
                              for a in CASHFLOW_ACCOUNTS],
    }


def load(today: date) -> dict:
    conn = sqlite3.connect(f"file:{storage.DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return build(conn, today)
    finally:
        conn.close()


def record_cashflow(body: dict, db: Path = storage.DB_PATH) -> tuple[int, dict]:
    """Validate and record one external transfer, as cashflow.py would.

    body: {"account", "date" (YYYY-MM-DD), "amount" (+deposit/-withdrawal),
    "note", "force"}. A matching row (same account/date/amount) is refused
    with 409 unless force is set - a duplicate silently corrupts every
    return figure, the same reason cashflow.py asks before recording one.
    """
    account = body["account"]
    if account not in CASHFLOW_ACCOUNTS:
        raise ValueError(f"unknown account {account}")
    day = date.fromisoformat(body["date"]).isoformat()
    amount = Decimal(str(body["amount"]))
    if amount == 0 or amount != amount.to_integral_value():
        raise ValueError("amount must be a nonzero whole number of won")
    note = (body.get("note") or "").strip()[:100] or None

    with storage.connect(db) as conn:
        dupes = conn.execute(
            "SELECT id FROM cashflows WHERE account = ? AND trade_date = ? "
            "AND amount = ?", (account, day, str(amount))).fetchall()
        if dupes and not body.get("force"):
            return 409, {"error": "같은 날짜·금액의 기록이 이미 있어요.",
                         "duplicate": True}
        storage.save_cashflow(conn, day, account, amount, note, "deposit")
    log.info("cashflow recorded: %s %s %s", account, day, amount)
    return 200, {"ok": True}


def save_isa_settings(body: dict, db: Path = storage.DB_PATH) -> tuple[int, dict]:
    """Store ISA details; only ISA_SETTINGS keys, validated by type.
    An empty value clears the setting."""
    values = {}
    for key, value in body.items():
        kind = ISA_SETTINGS.get(key)
        if kind is None:
            raise ValueError(f"unknown setting {key}")
        value = str(value).strip().replace(",", "") if value is not None else ""
        if not value:
            values[key] = None
        elif kind == "won":
            amount = Decimal(value)
            if amount < 0 or amount != amount.to_integral_value():
                raise ValueError(f"{key}: a whole number of won")
            values[key] = str(amount)
        elif kind == "date":
            values[key] = date.fromisoformat(value).isoformat()
        else:
            values[key] = value[:40]
    with storage.connect(db) as conn:
        storage.save_settings(conn, values, datetime.now(KST).isoformat())
    return 200, {"ok": True}


def dismiss_transfer(body: dict, db: Path = storage.DB_PATH) -> tuple[int, dict]:
    """Mark a suspected transfer as not one (body: account, date, amount)."""
    account = body["account"]
    if account not in CASHFLOW_ACCOUNTS:
        raise ValueError(f"unknown account {account}")
    day = date.fromisoformat(body["date"]).isoformat()
    with storage.connect(db) as conn:
        storage.save_transfer_review(conn, account, day, Decimal(str(body["amount"])),
                                     datetime.now(KST).isoformat())
    log.info("transfer dismissed: %s %s", account, day)
    return 200, {"ok": True}


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] != "/data.json":
            return super().do_GET()
        body = json.dumps(load(datetime.now(KST).date()), ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        handler = {"/cashflow": record_cashflow,
                   "/transfer/dismiss": dismiss_transfer,
                   "/settings/isa": save_isa_settings}.get(self.path)
        if handler is None:
            return self._json(404, {"error": "not found"})
        # A JSON body forces a CORS preflight, which this server never
        # answers, so another site open in the browser can't post here; the
        # Origin check covers browsers that send one anyway.
        origin = self.headers.get("Origin")
        if (self.headers.get("Content-Type", "").split(";")[0] != "application/json"
                or (origin and origin.split("://", 1)[-1] != self.headers.get("Host"))):
            return self._json(403, {"error": "forbidden"})
        try:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            status, payload = handler(body)
        except (ValueError, KeyError, TypeError, ArithmeticError) as e:
            status, payload = 400, {"error": f"잘못된 입력: {e}"}
        self._json(status, payload)

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        log.info(fmt, *args)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    storage.init()   # tables added since daily.py last ran; reads are mode=ro
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv \
        else DEFAULT_PORT
    server = ThreadingHTTPServer(("127.0.0.1", port),
                                 partial(Handler, directory=str(WEB_DIR)))
    log.info("tracker on http://127.0.0.1:%d", port)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
