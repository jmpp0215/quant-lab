"""Tests for unrecorded-transfer detection: each false alarm the old cash
check raised (exchange rate, distributions, settlement timing, trades it
had no order record for) gets a case, alongside the real transfers."""

from decimal import Decimal

from quant import transfers


def D(x) -> Decimal:
    return Decimal(str(x))


def snap(date, cash, positions=(), total=None):
    positions = [dict(p) for p in positions]
    value = sum((D(p["price"]) * p["qty"] for p in positions), D(0))
    return {"date": date, "cash": D(cash),
            "total": D(total) if total is not None else D(cash) + value,
            "positions": positions}


def pos(symbol, qty, price, currency=None):
    p = {"symbol": symbol, "qty": qty, "price": str(price)}
    if currency:
        p["currency"] = currency
    return p


def kinds(snaps, deposits=(), dividends=(), foreign_cash=False):
    s = transfers.steps(snaps, list(deposits), list(dividends), foreign_cash)
    return [(c["date"], c["kind"], round(c["amount"])) for c in transfers.classify(s)]


def history(*days, positions=()):
    return [snap(d, c, positions) for d, c in days]


class TestTransfers:
    def test_unrecorded_deposit_is_a_candidate(self):
        snaps = history(("d1", 1000000), ("d2", 2500000), ("d3", 2500000), ("d4", 2500000),
                        ("d5", 2500000))
        assert kinds(snaps) == [("d2", "transfer", 1500000)]

    def test_recorded_deposit_is_not(self):
        snaps = history(("d1", 1000000), ("d2", 2500000), ("d3", 2500000), ("d4", 2500000))
        assert kinds(snaps, deposits=[("d2", D(1500000))]) == []

    def test_withdrawal(self):
        snaps = history(("d1", 1000000), ("d2", 400000), ("d3", 400000), ("d4", 400000),
                        ("d5", 400000))
        assert kinds(snaps) == [("d2", "transfer", -600000)]

    def test_latest_steps_are_pending_until_a_reversal_could_show(self):
        snaps = history(("d1", 1000000), ("d2", 1000000), ("d3", 2000000))
        assert kinds(snaps) == [("d3", "pending", 1000000)]

    def test_small_changes_are_ignored(self):
        snaps = history(("d1", 1000000), ("d2", 1030000), ("d3", 1030000), ("d4", 1030000))
        assert kinds(snaps) == []


class TestTrades:
    def test_trade_without_order_record_is_explained_by_positions(self):
        # Bought 10 @ 10,000 at a limit 1% over the close: 101,000 left cash.
        snaps = [snap("d1", 500000), snap("d2", 399000, [pos("A", 10, 10000)]),
                 snap("d3", 399000, [pos("A", 10, 10000)]),
                 snap("d4", 399000, [pos("A", 10, 10000)])]
        assert kinds(snaps) == []

    def test_sell_out_uses_the_last_known_price(self):
        snaps = [snap("d1", 0, [pos("A", 10, 10000)]), snap("d2", 99000),
                 snap("d3", 99000), snap("d4", 99000)]
        assert kinds(snaps) == []

    def test_deposit_alongside_a_trade_still_shows(self):
        p = [pos("A", 10, 10000)]
        snaps = [snap("d1", 500000)] + [snap(d, 1400000, p) for d in ("d2", "d3", "d4", "d5")]
        assert kinds(snaps) == [("d2", "transfer", 1000000)]


class TestSettlementTiming:
    def test_cash_settling_three_snapshots_later_cancels_out(self):
        # T+2 across a holiday-adjacent gap: positions on d2, cash on d5.
        p = [pos("A", 100, 36700)]
        snaps = [snap("d1", 5000000)] + [snap(d, 5000000, p) for d in ("d2", "d3", "d4")] + [
            snap(d, 1330000, p) for d in ("d5", "d6", "d7", "d8")]
        assert [k for _, k, _ in kinds(snaps)] == ["timing", "timing"]

    def test_cash_settling_two_days_after_the_trade_cancels_out(self):
        # Positions move on d2, the cash only on d4 (T+2).
        p = [pos("A", 100, 36700)]
        snaps = [snap("d1", 5000000), snap("d2", 5000000, p), snap("d3", 5000000, p),
                 snap("d4", 1330000, p), snap("d5", 1330000, p), snap("d6", 1330000, p)]
        assert [k for _, k, _ in kinds(snaps)] == ["timing", "timing"]


class TestExchangeRate:
    def usd_account(self, date, rate, usd_cash, usd_price):
        # total and cash in KRW, positions in USD, as snapshot_overseas stores it
        return {"date": date, "cash": D(usd_cash) * D(rate),
                "total": (D(usd_cash) + D(usd_price) * 10) * D(rate),
                "positions": [pos("AMZN", 10, usd_price, "USD")]}

    def test_rate_move_on_usd_cash_is_not_a_transfer(self):
        snaps = [self.usd_account(d, r, 3758.47, 250)
                 for d, r in [("d1", 1393), ("d2", 1328), ("d3", 1340), ("d4", 1345)]]
        assert kinds(snaps, foreign_cash=True) == []

    def test_usd_deposit_still_shows(self):
        snaps = [self.usd_account("d1", 1300, 1000, 250)] + [
            self.usd_account(d, 1300, 3000, 250) for d in ("d2", "d3", "d4", "d5")]
        assert kinds(snaps, foreign_cash=True) == [("d2", "transfer", 2600000)]


class TestDividends:
    P = [pos("A", 1000, 10000)]

    def test_expected_dividend_after_tax_is_a_dividend(self):
        # 1000 shares x 70 won gross = 70,000; 15.4% withheld -> 59,220 arrives.
        snaps = [snap("d1", 100000, self.P), snap("d2", 159220, self.P),
                 snap("d3", 159220, self.P), snap("d4", 159220, self.P)]
        assert kinds(snaps, dividends=[("d2", D(70000))]) == [("d2", "dividend", 59220)]

    def test_much_more_than_the_dividend_is_still_a_transfer(self):
        snaps = [snap("d1", 100000, self.P)] + [
            snap(d, 1159220, self.P) for d in ("d2", "d3", "d4", "d5")]
        assert kinds(snaps, dividends=[("d2", D(70000))]) == [("d2", "transfer", 1059220)]

    def test_unannounced_small_distribution_is_under_the_threshold(self):
        snaps = [snap("d1", 100000, self.P), snap("d2", 120923, self.P),
                 snap("d3", 120923, self.P), snap("d4", 120923, self.P)]
        assert kinds(snaps) == []


class TestExpectedDividends:
    def test_shares_held_before_the_record_date_times_amount(self):
        snaps = [snap("2026-10-28", 0, [pos("A", 100, 1000)]),
                 snap("2026-10-30", 0, [pos("A", 300, 1000)])]
        events = [{"symbol": "A", "record_date": "2026-10-30", "amount": D(61),
                   "pay_date": "2026-11-04"},
                  {"symbol": "A", "record_date": "2026-12-31", "amount": D(0),
                   "pay_date": None},
                  {"symbol": "B", "record_date": "2026-10-30", "amount": D(5),
                   "pay_date": "2026-11-04"}]

        assert transfers.expected_dividends(snaps, events) == [("2026-11-04", D(6100))]


class TestDividendPayouts:
    SNAPS = [snap("2026-10-28", 0, [dict(pos("A", 100, 1000), name="에이")])]
    EVENTS = [{"symbol": "A", "record_date": "2026-10-30", "amount": D(100),
               "pay_date": "2026-11-04"},
              {"symbol": "A", "record_date": "2026-12-31", "amount": D(0),
               "pay_date": None}]

    def test_net_after_withholding_and_unset_amounts(self):
        paid, unset = transfers.dividend_payouts(self.SNAPS, self.EVENTS)

        assert (paid["name"], paid["gross"], paid["net"]) == ("에이", D(10000), D(8460))
        assert (unset["record_date"], unset["gross"], unset["net"]) == ("2026-12-31", None, None)

    def test_isa_is_tax_free(self):
        paid, _ = transfers.dividend_payouts(self.SNAPS, self.EVENTS, tax_free=True)
        assert paid["net"] == D(10000)
