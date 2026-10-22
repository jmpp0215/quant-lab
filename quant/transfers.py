"""Spot unrecorded deposits/withdrawals in snapshot history (pure).

Cash moves for four reasons: trades, distributions, exchange-rate moves
(on foreign cash), and external transfers. Only the last needs the user -
an unrecorded transfer silently reads as a gain or loss in every return
figure - so each snapshot-to-snapshot cash change is netted of the other
three before anything is flagged:

- trades: inferred from the change in held quantities x the new price, not
  from order records, so PBR (pbr_live.db), manual and overseas trades are
  covered too. Fills at a limit differ from the close, hence a tolerance.
- exchange rate: a foreign-cash account is compared in its own currency.
- distributions: a positive change close to the dividends expected to be
  paid in the step (announced per-share amount x shares held) is one.
- settlement timing: a change undone by an opposite one within a couple of
  snapshots (cash settling days after the positions moved) is not money
  arriving or leaving.

What is left, above a threshold, is a candidate for the user to confirm.
"""

from dataclasses import dataclass
from decimal import Decimal

from quant import tracker

THRESHOLD = Decimal("50000")          # KRW; below this, fees/rounding noise
TRADE_TOLERANCE = Decimal("0.03")     # of traded value: fill vs close drift
TRADE_FLOOR = Decimal("1000")         # KRW, commissions on small trades
PAIR_WINDOW = 3                       # snapshots either side (T+2 plus a holiday)
PAIR_MATCH = Decimal("0.2")           # |a + b| within 20% of |a|
DIVIDEND_RANGE = (Decimal("0.7"), Decimal("1.1"))   # x expected gross; covers 15.4% tax


@dataclass(frozen=True)
class Step:
    """One snapshot-to-snapshot change, in KRW at the later snapshot's rate."""
    date: str
    prev_date: str
    unexplained: Decimal
    traded: Decimal      # gross value of position changes
    dividends: Decimal   # expected gross distributions paid in (prev_date, date]


def steps(snapshots: list[dict], deposits: list[tuple[str, Decimal]],
          dividends: list[tuple[str, Decimal]], foreign_cash: bool = False) -> list[Step]:
    """Cash change per step netted of trades and recorded transfers.

    snapshots: {"date", "total", "cash", "positions"} (Decimal total/cash,
    positions as storage keeps them), any order. deposits: recorded
    (date, KRW amount). dividends: (pay date, expected gross KRW).
    foreign_cash: the account's cash is held in its positions' currency
    and valued in KRW (kis-main-overseas) - compared at the implied rate
    so a rate move isn't read as money moving.
    """
    snaps = sorted(snapshots, key=lambda s: s["date"])
    out = []
    for prev, cur in zip(snaps, snaps[1:], strict=False):
        rate_prev = rate_cur = Decimal("1")
        if foreign_cash:
            rate_prev, rate_cur = tracker.implied_rate(prev), tracker.implied_rate(cur)
            if not rate_prev or not rate_cur:
                continue   # no foreign positions to price the rate from
        held_prev = {p["symbol"]: p["qty"] for p in prev["positions"]}
        held_cur = {p["symbol"]: (p["qty"], Decimal(p["price"])) for p in cur["positions"]}
        price_prev = {p["symbol"]: Decimal(p["price"]) for p in prev["positions"]}

        bought = traded = Decimal("0")   # own currency
        for symbol in set(held_prev) | set(held_cur):
            qty, price = held_cur.get(symbol, (0, price_prev.get(symbol, Decimal("0"))))
            value = (qty - held_prev.get(symbol, 0)) * price
            bought += value
            traded += abs(value)

        moved = sum((a for d, a in deposits if prev["date"] < d <= cur["date"]),
                    Decimal("0")) / rate_cur
        paid = sum((a for d, a in dividends if prev["date"] < d <= cur["date"]),
                   Decimal("0"))
        delta = cur["cash"] / rate_cur - prev["cash"] / rate_prev
        out.append(Step(date=cur["date"], prev_date=prev["date"],
                        unexplained=(delta + bought - moved) * rate_cur,
                        traded=traded * rate_cur, dividends=paid))
    return out


def classify(series: list[Step]) -> list[dict]:
    """Label every step whose change isn't trading noise.

    kind: "transfer" (a candidate for the user), "pending" (looks like one
    but the snapshots that could reveal a settlement reversal aren't in
    yet), "timing", or "dividend". Steps within the trading tolerance or
    under THRESHOLD (that aren't dividends) are left out.
    """
    out = []
    for i, s in enumerate(series):
        u = s.unexplained
        if abs(u) <= TRADE_TOLERANCE * s.traded + TRADE_FLOOR:
            continue
        lo, hi = DIVIDEND_RANGE
        if s.dividends > 0 and lo * s.dividends <= u <= hi * s.dividends + TRADE_FLOOR:
            out.append({"date": s.date, "amount": u, "kind": "dividend"})
            continue
        if abs(u) < THRESHOLD:
            continue
        nearby = series[max(0, i - PAIR_WINDOW):i] + series[i + 1:i + 1 + PAIR_WINDOW]
        if any(abs(u + t.unexplained) <= PAIR_MATCH * abs(u) for t in nearby):
            kind = "timing"
        elif i >= len(series) - PAIR_WINDOW:
            kind = "pending"
        else:
            kind = "transfer"
        out.append({"date": s.date, "prev_date": s.prev_date, "amount": u, "kind": kind})
    return out


def expected_dividends(snapshots: list[dict], events: list[dict]) -> list[tuple[str, Decimal]]:
    """(pay date, gross KRW) one account should receive, for steps().

    events: {"symbol", "record_date", "amount" (per share, 0 = unset),
    "pay_date"}. Shares counted from the account's last snapshot before
    the record date - close enough to who held through the ex-date.
    """
    snaps = sorted(snapshots, key=lambda s: s["date"])
    out = []
    for e in events:
        if not e.get("pay_date") or e["amount"] <= 0:
            continue
        before = [s for s in snaps if s["date"] < e["record_date"]]
        if not before:
            continue
        qty = sum(p["qty"] for p in before[-1]["positions"] if p["symbol"] == e["symbol"])
        if qty:
            out.append((e["pay_date"], qty * e["amount"]))
    return out
