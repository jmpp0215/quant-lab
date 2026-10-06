"""Total-return price series for individual KRX stocks.

KIS 수정주가 for an individual stock re-bases only share-count events
(splits, consolidations, bonus issues) - cash dividends are NOT folded in
(verified live 2026-10-06: 005930 adjusted/raw ratio stayed 1.0000 across
its 372/374 KRW record dates, while ETF 102110's ratio stepped at each
distribution). pead_price_raw is raw closes, so a backtest on it silently
drops every cash dividend. These helpers put them back as reinvested
total return, the standard TR-index construction.

Dates are YYYYMMDD strings, matching the simulators that consume this.
"""
from bisect import bisect_right


def ex_date(record_date: str, trading_dates: list[str]) -> str | None:
    """The 배당락일 for a KSD record date, or None if it can't be placed.

    T+2 settlement: a buy settles two trading days later, so the last
    cum-dividend day is two settlement days before the last settlement day
    on or before the record date, and the ex-date is the trading day after
    it - i.e. the second-to-last trading day <= record_date. Covers a
    record date that isn't a trading day too (12/31: KRX closed, so a
    12/31 record date goes ex two trading days before it, not one).

    `trading_dates` must be the market calendar (ascending), not one
    symbol's dates - a halted stock's own series has gaps. A record date
    past the calendar's end returns None: the trading days between the
    calendar's end and the record date aren't known yet.
    """
    if not trading_dates or record_date > trading_dates[-1]:
        return None
    i = bisect_right(trading_dates, record_date)
    return trading_dates[i - 2] if i >= 2 else None


def total_return_series(prices: dict[str, float],
                        dividends: dict[str, float]) -> dict[str, float]:
    """Raw closes -> a total-return index (dividends reinvested on the
    ex-date), starting at the first raw close.

    tr[t] = tr[t-1] * (close[t] + dividend[t]) / close[t-1]

    `dividends` maps ex-date -> cash per share. An ex-date with no close for
    this symbol (halted that day) is credited on its next priced day - the
    holder of record still gets paid. Dividends on or before the first
    close are ignored: there is no prior close to measure them against.
    """
    dates = sorted(prices)
    if not dates:
        return {}
    pending = sorted((d, a) for d, a in dividends.items() if d > dates[0])
    k = 0
    out = {dates[0]: prices[dates[0]]}
    for prev, d in zip(dates, dates[1:], strict=False):
        paid = 0.0
        while k < len(pending) and pending[k][0] <= d:
            paid += pending[k][1]
            k += 1
        out[d] = out[prev] * (prices[d] + paid) / prices[prev]
    return out
