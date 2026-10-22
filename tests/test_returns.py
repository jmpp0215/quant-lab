"""Tests for principal-based and time-weighted account returns."""

from decimal import Decimal

from quant import returns


def D(x) -> Decimal:
    return Decimal(str(x))


def snaps(*pairs):
    return [(d, D(v)) for d, v in pairs]


PRINCIPAL = [("2026-08-28", D(100), "principal")]


class TestCompute:
    def test_two_ten_percent_gains_compound_to_twenty_one(self):
        r = returns.compute(snaps(("2026-08-28", 100), ("2026-08-31", 110),
                                  ("2026-09-01", 121)), PRINCIPAL)

        assert r.principal_return == D("0.21")
        assert r.time_weighted == D("0.21")
        assert (r.start, r.end, r.money_in, r.value) == (
            "2026-08-28", "2026-09-01", D(100), D(121))

    def test_two_ten_percent_losses_compound_to_minus_nineteen(self):
        r = returns.compute(snaps(("2026-08-31", 90), ("2026-09-01", 81)),
                            PRINCIPAL)

        assert r.principal_return == D("-0.19")
        assert r.time_weighted == D("-0.19")

    def test_deposit_raises_money_in_but_not_time_weighted_return(self):
        # +10%, then 100 deposited, then flat: the strategy made 10%, the
        # money put in (200) made 10 on 200 = 5%.
        flows = PRINCIPAL + [("2026-09-01", D(100), "deposit")]
        r = returns.compute(snaps(("2026-08-31", 110), ("2026-09-01", 210)),
                            flows)

        assert r.money_in == D(200)
        assert r.principal_return == D("0.05")
        assert r.time_weighted == D("0.1")

    def test_withdrawal_lowers_money_in(self):
        flows = PRINCIPAL + [("2026-09-01", D(-50), "deposit")]
        r = returns.compute(snaps(("2026-08-31", 110), ("2026-09-01", 60)),
                            flows)

        assert r.money_in == D(50)
        assert r.principal_return == D("0.2")
        assert r.time_weighted == D("0.1")

    def test_history_before_the_principal_date_is_ignored(self):
        flows = PRINCIPAL + [("2026-08-14", D(999), "deposit")]
        r = returns.compute(snaps(("2026-08-25", 500), ("2026-08-28", 100),
                                  ("2026-08-31", 110)), flows)

        assert r.money_in == D(100)
        assert r.principal_return == D("0.1")

    def test_cost_basis_principal_counts_unrealised_gain_in_principal_only(self):
        # Bought for 100, worth 120 on the principal date, then +10%.
        flows = [("2026-10-21", D(100), "principal")]
        r = returns.compute(snaps(("2026-10-21", 120), ("2026-10-23", 132)),
                            flows)

        assert r.principal_return == D("0.32")
        assert r.time_weighted == D("0.1")

    def test_opening_balance_rows_are_not_money_in(self):
        # opening_balance is a cash-reconciliation reference, not a transfer.
        flows = PRINCIPAL + [("2026-09-10", D(5000), "opening_balance")]
        r = returns.compute(snaps(("2026-09-10", 110)), flows)

        assert r.money_in == D(100)

    def test_latest_principal_row_wins(self):
        flows = [("2026-08-01", D(50), "principal")] + PRINCIPAL
        r = returns.compute(snaps(("2026-08-31", 110)), flows)

        assert (r.start, r.money_in) == ("2026-08-28", D(100))

    def test_without_principal_only_time_weighted_is_reported(self):
        r = returns.compute(snaps(("2026-08-28", 100), ("2026-08-31", 110)),
                            [("2026-08-14", D(100), "deposit")])

        assert r.principal_return is None
        assert r.money_in is None
        assert r.time_weighted == D("0.1")
        assert r.start == "2026-08-28"

    def test_no_snapshots_after_principal_means_no_result(self):
        assert returns.compute(snaps(("2026-08-25", 100)), PRINCIPAL) is None
