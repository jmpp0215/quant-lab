"""Tests for portfolio weighting.

Most of these check mathematical identities rather than specific numbers:
risk contributions must sum to portfolio volatility, weights must sum to
one, risk parity must equalise contributions. Identities hold for any
valid input, so they catch errors that hand-picked examples miss.
"""

from decimal import Decimal

import pytest

import allocation


def make_candles(prices: list[str]) -> list[dict]:
    """Newest-first candles from a price series."""
    return [{"closePrice": p} for p in prices]


# A covariance matrix with deliberately unequal volatilities and one pair
# far less correlated than the others, so inverse-vol and risk parity
# cannot coincide.
COV = [
    [0.6241, 0.1500, 0.0400],   # vol 79%
    [0.1500, 0.1764, 0.0300],   # vol 42%
    [0.0400, 0.0300, 0.0400],   # vol 20%
]


class TestDailyReturns:
    def test_computes_returns_newest_first(self):
        candles = make_candles(["110", "100"])
        assert allocation.daily_returns(candles) == pytest.approx([0.1])

    def test_respects_window(self):
        candles = make_candles([str(100 + i) for i in range(50)])
        assert len(allocation.daily_returns(candles, window=10)) == 10

    def test_skips_zero_denominator(self):
        candles = make_candles(["110", "0", "100"])
        returns = allocation.daily_returns(candles)
        assert all(r == r for r in returns)   # no NaN
        assert len(returns) == 1


class TestCovariance:
    def test_diagonal_is_variance(self):
        returns = {
            "A": [0.01, -0.02, 0.03, -0.01, 0.02] * 10,
            "B": [0.02, -0.01, 0.01, -0.02, 0.01] * 10,
        }
        cov = allocation.covariance_matrix(returns, ["A", "B"])
        assert cov[0][0] > 0
        assert cov[1][1] > 0

    def test_matrix_is_symmetric(self):
        returns = {
            "A": [0.01, -0.02, 0.03, -0.01, 0.02] * 10,
            "B": [0.02, -0.01, 0.01, -0.02, 0.01] * 10,
        }
        cov = allocation.covariance_matrix(returns, ["A", "B"])
        assert cov[0][1] == pytest.approx(cov[1][0])

    def test_aligns_to_shortest_series(self):
        returns = {"A": [0.01] * 50, "B": [0.01] * 20}
        cov = allocation.covariance_matrix(returns, ["A", "B"])
        assert len(cov) == 2   # does not raise on unequal lengths


class TestRiskContributions:
    def test_contributions_sum_to_portfolio_volatility(self):
        # This identity is what makes the decomposition meaningful: the
        # parts must exactly account for the whole.
        weights = [0.5, 0.3, 0.2]
        total = allocation.portfolio_volatility(weights, COV)
        parts = allocation.risk_contributions(weights, COV)
        assert sum(parts) == pytest.approx(total)

    def test_equal_weights_give_unequal_contributions(self):
        # The premise of the whole module: equal money is not equal risk.
        weights = [1 / 3] * 3
        parts = allocation.risk_contributions(weights, COV)
        assert max(parts) > 3 * min(parts)

    def test_zero_weights_contribute_nothing(self):
        parts = allocation.risk_contributions([1.0, 0.0, 0.0], COV)
        assert parts[1] == pytest.approx(0.0)
        assert parts[2] == pytest.approx(0.0)


class TestRiskParity:
    def test_weights_sum_to_one(self):
        weights = allocation.risk_parity(COV)
        assert sum(weights) == pytest.approx(1.0)

    def test_all_weights_positive(self):
        # A negative weight would be a short position, which this strategy
        # does not take.
        weights = allocation.risk_parity(COV)
        assert all(w > 0 for w in weights)

    def test_equalises_risk_contributions(self):
        weights = allocation.risk_parity(COV)
        parts = allocation.risk_contributions(weights, COV)
        assert max(parts) == pytest.approx(min(parts), rel=1e-4)

    def test_higher_volatility_gets_lower_weight(self):
        weights = allocation.risk_parity(COV)
        # COV is ordered from most to least volatile.
        assert weights[0] < weights[1] < weights[2]

    def test_lowers_portfolio_volatility_versus_equal(self):
        equal = [1 / 3] * 3
        parity = allocation.risk_parity(COV)
        assert (allocation.portfolio_volatility(parity, COV)
                < allocation.portfolio_volatility(equal, COV))

    def test_identical_assets_give_equal_weights(self):
        # With the same variance and no correlation differences there is
        # nothing to distinguish the assets.
        uniform = [[0.04 if i == j else 0.0 for j in range(3)]
                   for i in range(3)]
        weights = allocation.risk_parity(uniform)
        assert all(w == pytest.approx(1 / 3, rel=1e-3) for w in weights)


class TestDecimalConversion:
    def test_produces_decimals_summing_to_about_one(self):
        weights = allocation.risk_parity(COV)
        converted = allocation.to_decimal_weights(["A", "B", "C"], weights)
        assert all(isinstance(w, Decimal) for w in converted.values())
        assert sum(converted.values()) == pytest.approx(Decimal("1"),
                                                        abs=Decimal("0.001"))