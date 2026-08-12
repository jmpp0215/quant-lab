"""Strategy configuration. Everything tunable lives here."""

from decimal import Decimal

# Universe: every candidate the strategy may hold. Membership is chosen for
# low mutual correlation, not recent performance - a weak performer simply
# does not get selected, and stays available for when its momentum turns.
UNIVERSE = {
    "102110": "TIGER 코스피200",
    "466920": "SOL 조선TOP3플러스",
    "091170": "KODEX 은행",
    "133690": "TIGER 미국나스닥100",
    "195930": "TIGER 유로스탁스50(합성H)",
    "371160": "TIGER 차이나항셍테크",
    "484790": "KODEX 미국30년국채액티브(H)",
    "459580": "KODEX CD금리액티브(합성)",
}

# Tracked for research only - never traded. 487240 was excluded from the
# universe because it ran 0.75 correlated with 코스피200 and 0.68 with 조선,
# so holding both added little diversification. Keeping its candles lets us
# check later whether that call cost us anything.
WATCH_ONLY = {
    "487240": "KODEX AI전력핵심설비",
    "241180": "TIGER 일본니케이225",
}

# The risk-free proxy. Absolute momentum compares against this rather than
# against zero, so a symbol must beat cash to be worth holding.
CASH_SYMBOL = "459580"

# How many positions to hold, equally weighted.
TOP_N = 3

# Lookback in months for the momentum ranking.
LOOKBACK_MONTHS = 12

# Months to skip at the recent end, avoiding short-term reversal.
# 0 = plain 12-month momentum, 1 = the standard "12-1" formulation.
SKIP_MONTHS = 1

# Trading days of history to keep locally.
HISTORY_DAYS = 300


def all_symbols() -> list[str]:
    """Every symbol we fetch candles for, traded or not."""
    return list(UNIVERSE) + list(WATCH_ONLY)