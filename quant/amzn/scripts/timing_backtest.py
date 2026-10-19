"""Does picking the day to buy within each month beat buying on day one?

Fetches AMZN daily bars from KIS (read-only), runs quant/amzn/timing.py's
pickers over every complete month, and prints each one's share gain versus
the first-trading-day baseline. Places no orders.

CLI:
    python -m quant.amzn.scripts.timing_backtest [--account kis-main] [--years 10]
"""
import argparse
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from quant import accounts
from quant.amzn import timing
from quant.amzn.config import AmznConfig
from quant.amzn.rate_limit import RateLimiter

ET = ZoneInfo("America/New_York")
WARMUP_BARS = 60  # RSI/drawdown need history before the first scored month

log = logging.getLogger("amzn.timing_backtest")


def fetch_bars(client, symbol: str, exchange: str, start: date) -> list[timing.Bar]:
    """Pages backwards 100 bars at a time until `start`. Drops the current
    US session's bar, which is still in progress."""
    today_et = datetime.now(ET).date()
    rate_limiter = RateLimiter()
    by_day: dict[date, timing.Bar] = {}
    end_ymd = ""
    while True:
        rate_limiter.wait()
        rows = client.daily_price_overseas(symbol, exchange, end_ymd).get("output2", [])
        rows = [r for r in rows if r.get("xymd")]
        if not rows:
            break
        for r in rows:
            d = date(int(r["xymd"][:4]), int(r["xymd"][4:6]), int(r["xymd"][6:]))
            by_day[d] = timing.Bar(d, float(r["open"]), float(r["clos"]))
        oldest = min(by_day)
        if oldest <= start:
            break
        end_ymd = (oldest - timedelta(days=1)).strftime("%Y%m%d")
    return [by_day[d] for d in sorted(by_day) if start <= d < today_et]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="AMZN monthly-accumulation timing backtest")
    parser.add_argument("--account", default="kis-main")
    parser.add_argument("--symbol", default="AMZN")
    parser.add_argument("--years", type=int, default=10)
    args = parser.parse_args()

    today_et = datetime.now(ET).date()
    start = date(today_et.year - args.years, today_et.month, 1) - timedelta(days=WARMUP_BARS * 2)
    client = accounts.get_client(args.account)
    bars = fetch_bars(client, args.symbol, AmznConfig().exchange, start)
    log.info("%d bars %s ~ %s", len(bars), bars[0].day, bars[-1].day)

    # Score complete months only, after warmup: drop the first (partial)
    # month and the current one (its month-end deadline hasn't happened).
    groups = timing.month_groups(bars)
    current = (today_et.year, today_et.month)
    months = [m for m, idx in groups.items()
              if idx[0] >= WARMUP_BARS and m != current]

    pickers = {
        "월말 매수 (기다림 비용)": timing.last_day,
        "RSI<30 다음날": timing.triggered(timing.rsi_below(30)),
        "RSI<40 다음날": timing.triggered(timing.rsi_below(40)),
        "20일고점 -5% 다음날": timing.triggered(timing.drawdown_from_high(20, 0.05)),
        "20일고점 -8% 다음날": timing.triggered(timing.drawdown_from_high(20, 0.08)),
        "사후 월 최저시가 (상한)": timing.hindsight_low,
    }
    baseline = timing.simulate(bars, months, timing.first_day)
    years = sorted({m[0] for m in months})

    print(f"\n{args.symbol}: {months[0][0]}-{months[0][1]:02d} ~ {months[-1][0]}-{months[-1][1]:02d}, "
          f"{len(months)}개월, 기준선 = 매월 첫 거래일 시가")
    print(f"{'규칙':<24}{'주식수 차이':>10}{'월 승률':>9}{'이긴 해':>9}{'발동률':>8}")
    for name, picker in pickers.items():
        buys = timing.simulate(bars, months, picker)
        c = timing.compare(buys, baseline)
        year_wins = sum(v > 0 for v in c.yearly.values())
        fired = sum(buy.day != bars[groups[buy.month][-1]].day for buy in buys)
        print(f"{name:<24}{c.share_gain:>+10.2%}{c.monthly_win_rate:>9.0%}"
              f"{year_wins:>5}/{len(years)}{fired / len(buys):>8.0%}")

    print("\n연도별 주식수 차이 (기준선 대비)")
    print(f"{'규칙':<24}" + "".join(f"{y:>7}" for y in years))
    for name, picker in pickers.items():
        c = timing.compare(timing.simulate(bars, months, picker), baseline)
        print(f"{name:<24}" + "".join(f"{c.yearly[y]:>+7.1%}" for y in years))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
