"""Account returns measured against the money put in (pure).

Two figures, because they answer different questions:

- principal return: (value now - net money in) / net money in. "What did
  my money become?" - realised and unrealised gains and idle cash all
  count, and compounding is built in (100 -> 110 -> 121 is +21%, not +20%).
  A broker app's holdings return (P&L / cost of what is held now) is not
  this: selling drops the realised gain and rebuying resets the cost.
- time-weighted return: daily snapshot returns chained, each with that
  period's transfers taken out. "How did the strategy do?" - unaffected by
  when or how much was deposited.

The starting point is a cashflow row of kind "principal": the account
value taken as the money put in on that date (no transfer accompanied it,
so it is never netted out of cash reconciliation). Deposits/withdrawals
(kind "deposit") after that date add to it.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Returns:
    start: str
    end: str
    money_in: Decimal | None       # None: no principal recorded
    value: Decimal
    principal_return: Decimal | None
    time_weighted: Decimal


def compute(snapshots: list[tuple[str, Decimal]],
            flows: list[tuple[str, Decimal, str]]) -> Returns | None:
    """snapshots: (date, total), any order. flows: (date, amount, kind).

    Without a principal row, the time-weighted return runs from the first
    snapshot and the principal return is None - guessing the money put in
    from a snapshot would silently count every earlier gain as principal.
    """
    principal = sorted((d, a) for d, a, k in flows if k == "principal")
    start, money_in = principal[-1] if principal else (None, None)

    series = sorted(s for s in snapshots if start is None or s[0] >= start)
    if not series:
        return None
    if start is None:
        start, prev_value = series[0]
        series = series[1:]
    else:
        prev_value = money_in
        if series[0][0] == start:
            series = series[1:]

    deposits = sorted((d, a) for d, a, k in flows if k == "deposit" and d > start)
    if money_in is not None:
        money_in += sum((a for _, a in deposits), Decimal("0"))

    growth = Decimal("1")
    prev_date = start
    for date, value in series:
        moved = sum((a for d, a in deposits if prev_date < d <= date), Decimal("0"))
        # Transfers are taken to arrive just before this snapshot, so they
        # earn nothing in the period they land in.
        growth *= (value - moved) / prev_value
        prev_date, prev_value = date, value

    principal_return = ((prev_value - money_in) / money_in
                        if money_in else None)
    return Returns(start=start, end=prev_date, money_in=money_in,
                   value=prev_value, principal_return=principal_return,
                   time_weighted=growth - 1)
