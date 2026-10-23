"""Long daily price history for whatever the accounts hold today, plus the
proxy indices and USD/KRW - the input to the tracker's crisis replay and
holding-based return range (quant/stress.py) - and the US and Korean fear &
greed inputs (quant/sentiment.py), refetched daily.

    python price_history.py      fetch what's missing or stale (daily.py does this after the close)

Source is Yahoo via FinanceDataReader (adjusted close, no login): KIS only
returns 100 rows per call and the Toss/KIS candle caches start in 2020,
too late for 2008. Each ticker is one request, cached in
data/price_history/<ticker>.csv and refetched only when a week old, with a
pause between requests. tickers.json maps each holding to its Yahoo ticker
(a Korean code is tried as .KS, then .KQ); a holding Yahoo doesn't have is
remembered as null and retried after a week, and the tracker proxies it.
"""

import json
import logging
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from quant import market, sentiment, storage, stress

DIR = storage.DB_PATH.parent / "price_history"
TICKERS = DIR / "tickers.json"
START = "2004-01-01"   # the Korean fear & greed index reaches back through 2008
MAX_AGE_DAYS = 7
PAUSE = 1.0

log = logging.getLogger("price_history")


def held_symbols(conn: sqlite3.Connection) -> dict[str, str]:
    """Symbol -> price currency for every position in each account's latest snapshot."""
    out = {}
    for (positions,) in conn.execute(
            "SELECT positions FROM portfolio p WHERE trade_date = "
            "(SELECT MAX(trade_date) FROM portfolio q WHERE q.account = p.account)"):
        for p in json.loads(positions):
            out[p["symbol"]] = p.get("currency", "KRW")
    return out


def candidates(symbol: str, currency: str) -> list[str]:
    if currency == "KRW":
        return [f"{symbol}.KS", f"{symbol}.KQ"]
    return [symbol.replace("/", "-")]


def load(ticker: str) -> tuple[list[str], list[float]] | None:
    path = DIR / f"{ticker}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    return (df["date"].tolist(), df["close"].astype(float).tolist())


def load_all() -> tuple[dict, dict]:
    """(series by ticker, holding symbol -> ticker or None) from the cache."""
    tickers = json.loads(TICKERS.read_text())["map"] if TICKERS.exists() else {}
    series = {}
    for t in set(stress.PROXIES) | set(sentiment.TICKERS) | set(sentiment.KR_TICKERS) | {t for t in tickers.values() if t}:
        s = load(t)
        if s and s[0]:
            series[t] = s
    return series, tickers


def _fresh(path: Path, today: date, max_age: int = MAX_AGE_DAYS) -> bool:
    return path.exists() and datetime.fromtimestamp(path.stat().st_mtime, market.KST).date() > today - timedelta(days=max_age)


def _fetch(ticker: str) -> pd.Series | None:
    import FinanceDataReader as fdr
    try:
        df = fdr.DataReader(f"YAHOO:{ticker}", START)
    except Exception:
        log.info("no Yahoo data for %s", ticker)
        return None
    close = df["Adj Close"].dropna() if "Adj Close" in df else None
    return close if close is not None and len(close) else None


def _save(ticker: str, close: pd.Series) -> None:
    pd.DataFrame({"date": close.index.strftime("%Y-%m-%d"), "close": close.values}) \
        .to_csv(DIR / f"{ticker}.csv", index=False)


def run(today: date) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    state = json.loads(TICKERS.read_text()) if TICKERS.exists() else {"map": {}, "checked": {}}
    with storage.connect() as conn:
        held = held_symbols(conn)
    # The fear & greed inputs are refetched every day; the crisis replay's
    # weekly refresh is enough for everything else.
    daily = set(sentiment.TICKERS) | set(sentiment.KR_TICKERS)
    for t in dict.fromkeys(stress.PROXIES + sentiment.TICKERS + sentiment.KR_TICKERS):
        if not _fresh(DIR / f"{t}.csv", today, 1 if t in daily else MAX_AGE_DAYS):
            close = _fetch(t)
            if close is not None:
                _save(t, close)
            time.sleep(PAUSE)
    for symbol, ccy in held.items():
        known = state["map"].get(symbol)
        if known and _fresh(DIR / f"{known}.csv", today):
            continue
        checked = state["checked"].get(symbol)
        if symbol in state["map"] and known is None and checked and \
                date.fromisoformat(checked) > today - timedelta(days=MAX_AGE_DAYS):
            continue
        found = None
        for t in ([known] if known else candidates(symbol, ccy)):
            close = _fetch(t)
            time.sleep(PAUSE)
            if close is not None:
                _save(t, close)
                found = t
                break
        state["map"][symbol] = found
        state["checked"][symbol] = today.isoformat()
    TICKERS.write_text(json.dumps(state, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(datetime.now(market.KST).date())
    series, tickers = load_all()
    missing = sorted(s for s, t in tickers.items() if t is None)
    print(f"{len(series)} series cached; no Yahoo history (proxied): {missing or 'none'}")
    sys.exit(0)
