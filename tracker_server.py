"""Asset tracker: serves tracker_web/ and its data as one JSON document.

    python tracker_server.py                 http://127.0.0.1:8765
    python tracker_server.py --port 9000

Reads with mode=ro; the one write is POST /cashflow (an external
deposit/withdrawal, the same row cashflow.py records). Nothing on the
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

from quant import returns, storage, tracker
from quant.stock_factors.scripts import pbr_storage

log = logging.getLogger("tracker")

WEB_DIR = Path(__file__).parent / "tracker_web"
DEFAULT_PORT = 8765
KST = ZoneInfo("Asia/Seoul")

# Goal: None hides the projection. Monthly contribution and assumed return
# are planning inputs, not measured figures.
GOAL_TARGET = None           # KRW, e.g. Decimal("100000000")
GOAL_MONTHLY = Decimal("0")  # KRW added per month
GOAL_ANNUAL_RETURN = Decimal("0.05")

# ISA contribution limits. None hides the section - set after checking the
# current rules (the 2026 reform may have changed them). Paid-in amounts are
# entered by hand: cashflows can't supply them - the "principal" row is a
# cost basis on the tracker start date, not what was paid in.
ISA_ANNUAL_LIMIT = None      # KRW per calendar year
ISA_TOTAL_LIMIT = None       # KRW over the account's life
ISA_PAID_THIS_YEAR = Decimal("0")
ISA_PAID_TOTAL = Decimal("0")

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

CASHFLOW_ACCOUNTS = ("kis-isa", "kis-main", "kis-main-overseas", "toss-bot")
LABELS = {"kis-isa": "ISA (KIS)", "kis-main": "KIS 국내",
          "kis-main-overseas": "KIS 해외", "toss-bot": "토스"}


def _num(d: Decimal | None) -> float | None:
    return None if d is None else float(d)


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

    account_rows = []
    for a in names:
        r = returns.compute(by_account[a], [
            (f["trade_date"], Decimal(f["amount"]), f["kind"]) for f in flows
            if f["account"] == a])
        series = by_account[a]
        prev = series[-2][1] if len(series) > 1 else None
        account_rows.append({
            "account": a, "label": LABELS.get(a, a),
            "value": _num(series[-1][1]), "as_of": series[-1][0],
            "day_change": _num(series[-1][1] - prev) if prev is not None else None,
            "since": r.start, "money_in": _num(r.money_in),
            "principal_return": _num(r.principal_return),
            "time_weighted": _num(r.time_weighted),
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

    isa = None
    if ISA_ANNUAL_LIMIT is not None or ISA_TOTAL_LIMIT is not None:
        isa = {"annual_limit": _num(ISA_ANNUAL_LIMIT),
               "total_limit": _num(ISA_TOTAL_LIMIT),
               "this_year": _num(ISA_PAID_THIS_YEAR),
               "total": _num(ISA_PAID_TOTAL)}

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
    upcoming.sort(key=lambda u: u["date"])
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
        "rebalances": sorted(rebalances, key=lambda r: r["date"]),
        "upcoming": upcoming,
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
        if self.path != "/cashflow":
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
            status, payload = record_cashflow(body)
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
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv \
        else DEFAULT_PORT
    server = ThreadingHTTPServer(("127.0.0.1", port),
                                 partial(Handler, directory=str(WEB_DIR)))
    log.info("tracker on http://127.0.0.1:%d", port)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
