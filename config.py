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
    "379790": "RISE 유로스탁스50",
    "371160": "TIGER 차이나항셍테크",
    "484790": "KODEX 미국30년국채액티브(H)",
    "497880": "SOL CD금리&머니마켓액티브",
}

# Tracked for research only - never traded. 487240 was excluded from the
# universe because it ran 0.75 correlated with 코스피200 and 0.68 with 조선,
# so holding both added little diversification. Keeping its candles lets us
# check later whether that call cost us anything.
WATCH_ONLY = {
    "487240": "KODEX AI전력핵심설비",
    "241180": "TIGER 일본니케이225",
    "195930": "TIGER 유로스탁스50(합성H)",
    "459580": "KODEX CD금리액티브(합성)",
}
# Annual distribution yield per symbol, used to approximate total return.
# Toss candles are price-only, so a high-yield symbol would otherwise rank
# unfairly low - the ex-dividend drop shows up in the price while the
# payout does not. These are rough figures; update them yearly from each
# fund's disclosure page.
DIVIDEND_YIELD = {
    "102110": Decimal("0.0090"),   # 코스피200
    "466920": Decimal("0.0018"),   # 조선TOP3
    "091170": Decimal("0.0376"),   # 은행
    "133690": Decimal("0.0051"),   # 나스닥100
    "379790": Decimal("0.0388"),   # 유로스탁스50 (RISE, 실물)
    "371160": Decimal("0.0095"),   # 차이나항셍테크
    "484790": Decimal("0.0522"),   # 미국30년국채
    "497880": Decimal("0.0302"),   # CD금리&머니마켓
    "487240": Decimal("0.0012"),   # AI전력 (watch only)
    "241180": Decimal("0.0097"),   # 니케이225 (watch only)
    "195930": Decimal("0.0170"),   # 유로스탁스50(합성H) (watch only)
    "459580": Decimal("0.0271"),   # CD금리액티브(합성) (watch only)
}

# Half the quoted spread, measured from live order books on 2026-08-18.
# A buy lifts the ask and a sell hits the bid, so a round trip costs the
# full spread; this is the one-way share of it.
HALF_SPREAD = {
    "102110": Decimal("0.00005"),   # 109,615 / 109,620
    "466920": Decimal("0.00025"),
    "091170": Decimal("0.00016"),   # 16,175 grid, 5-won tick
    "133690": Decimal("0.00003"),   # 187,985 grid
    "379790": Decimal("0.00030"),
    "371160": Decimal("0.00069"),   # 7,270 / 7,280 - widest in the universe
    "484790": Decimal("0.00030"),
    "497880": Decimal("0.00005"),
}

DEFAULT_HALF_SPREAD = Decimal("0.0005")
# Korean-listed ETFs tracking foreign assets are taxed at 15.4% on
# realised gains; domestic equity ETFs are exempt. Which side of that line
# a symbol falls on drives most of the cost of extra rebalancing.
FOREIGN_ETF = {"133690", "379790", "371160", "484790"}

GAINS_TAX_RATE = Decimal("0.154")

# Domestic sales carry a transaction tax; ETFs are exempt from it, so this
# stays zero and exists only to make the omission explicit.
TRANSACTION_TAX_RATE = Decimal("0")

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

TRANCHES = (0, 5, 10)   # nth trading day of the month, zero-indexed

def tranche_cash_share(total_cash: Decimal) -> Decimal:
    return total_cash / len(TRANCHES)

def is_etf(symbol: str) -> bool:
    return symbol in UNIVERSE or symbol in WATCH_ONLY

def all_symbols() -> list[str]:
    """Every symbol we fetch candles for, traded or not."""
    return list(UNIVERSE) + list(WATCH_ONLY)