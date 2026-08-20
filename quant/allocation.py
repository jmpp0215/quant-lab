"""Portfolio weighting schemes.

Equal weighting allocates equal money, not equal risk. With a universe
whose volatilities range from 20% to 79% annualised, an equal split hands
two thirds of the portfolio's risk to a single name - the diversification
is nominal rather than real.
"""

import logging
from decimal import Decimal

log = logging.getLogger(__name__)

TRADING_DAYS = 252
DEFAULT_WINDOW = 120
MAX_ITERATIONS = 1000
TOLERANCE = 1e-8


def daily_returns(candles: list[dict], window: int = DEFAULT_WINDOW
                  ) -> list[float]:
    """Simple daily returns, newest first.

    Floats are fine here: this feeds a covariance estimate that is itself
    an approximation, unlike prices where exactness matters.
    """
    closes = [float(c["closePrice"]) for c in candles[:window + 1]]
    return [
        (closes[i] - closes[i + 1]) / closes[i + 1]
        for i in range(len(closes) - 1)
        if closes[i + 1] != 0
    ]


def covariance_matrix(returns: dict[str, list[float]],
                      symbols: list[str]) -> list[list[float]]:
    """Annualised covariance, aligned to the shortest available history."""
    length = min(len(returns[s]) for s in symbols)
    series = {s: returns[s][:length] for s in symbols}
    means = {s: sum(series[s]) / length for s in symbols}

    def cov(a: str, b: str) -> float:
        total = sum(
            (series[a][k] - means[a]) * (series[b][k] - means[b])
            for k in range(length)
        )
        return total / (length - 1) * TRADING_DAYS

    return [[cov(a, b) for b in symbols] for a in symbols]


def portfolio_volatility(weights: list[float],
                         cov: list[list[float]]) -> float:
    n = len(weights)
    variance = sum(
        weights[i] * weights[j] * cov[i][j]
        for i in range(n) for j in range(n)
    )
    return variance ** 0.5


def risk_contributions(weights: list[float],
                       cov: list[list[float]]) -> list[float]:
    """Each asset's share of total portfolio volatility.

    These sum exactly to the portfolio volatility, which is what makes
    the decomposition meaningful rather than merely indicative.
    """
    n = len(weights)
    sigma = portfolio_volatility(weights, cov)
    if sigma == 0:
        return [0.0] * n

    marginal = [
        sum(cov[i][j] * weights[j] for j in range(n))
        for i in range(n)
    ]
    return [weights[i] * marginal[i] / sigma for i in range(n)]


def risk_parity(cov: list[list[float]],
                max_iterations: int = MAX_ITERATIONS,
                tolerance: float = TOLERANCE) -> list[float]:
    """Weights at which every asset contributes equal risk.

    Solved by multiplicative update: each weight is scaled by the ratio of
    its target risk contribution to its actual one, then renormalised.
    There is no closed form, but this converges reliably for a positive
    definite covariance matrix and needs no optimiser.
    """
    n = len(cov)
    weights = [1.0 / n] * n

    for iteration in range(max_iterations):
        marginal = [
            sum(cov[i][j] * weights[j] for j in range(n))
            for i in range(n)
        ]
        variance = sum(weights[i] * marginal[i] for i in range(n))
        target = variance / n

        updated = []
        for i in range(n):
            contribution = weights[i] * marginal[i]
            if contribution <= 0:
                updated.append(weights[i])
                continue
            updated.append(weights[i] * (target / contribution) ** 0.5)

        total = sum(updated)
        updated = [w / total for w in updated]

        shift = max(abs(updated[i] - weights[i]) for i in range(n))
        weights = updated

        if shift < tolerance:
            log.debug("risk parity converged in %d iterations", iteration + 1)
            return weights

    log.warning("risk parity did not converge in %d iterations",
                max_iterations)
    return weights


def to_decimal_weights(symbols: list[str],
                       weights: list[float]) -> dict[str, Decimal]:
    """Convert to Decimal for downstream order sizing."""
    return {
        sym: Decimal(str(w)).quantize(Decimal("0.0001"))
        for sym, w in zip(symbols, weights, strict=True)
    }