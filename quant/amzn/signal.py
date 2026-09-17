"""AMZN own-history signal calculations: RSI(14) on daily closes, and
percentile rank of today's PER/PBR against AMZN's own accumulated history.

Deliberately NOT in config.py: these are read-time classification
thresholds, not collection parameters. AmznConfig.get_hash() is stamped on
every amzn_price_daily row to track which *collection* config produced it -
folding threshold constants into that dataclass would change get_hash() for
future rows every time a threshold is tuned, even though thresholds don't
affect what KIS returned. Plain module constants avoid that blast radius.

Collection-only history, no backfill (see quant/amzn/README.md): RSI needs
MIN_RSI_SAMPLES closes and percentile needs MIN_PERCENTILE_SAMPLES rows
before either produces a real number - callers must gate on these and show
an "insufficient data" state until then.
"""

RSI_PERIOD = 14
MIN_RSI_SAMPLES = RSI_PERIOD + 1  # need `period` deltas to seed Wilder's average

RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

MIN_PERCENTILE_SAMPLES = 20
VALUATION_UNDERVALUED_PCTL = 30
VALUATION_OVERVALUED_PCTL = 70


def compute_rsi(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    """Wilder's smoothed RSI over `closes` (oldest-first, one close per
    day). None if fewer than period+1 closes are available."""
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def percentile_rank(history: list[float], current: float) -> float:
    """% of values in `history` that are <= current. 0 = lowest ever seen,
    100 = highest ever seen. Caller decides whether `current` is included
    in `history`. Raises ValueError on empty history - callers must gate
    on MIN_PERCENTILE_SAMPLES first."""
    if not history:
        raise ValueError("percentile_rank: history is empty")
    n_le = sum(1 for v in history if v <= current)
    return 100 * n_le / len(history)


def classify_rsi(rsi: float) -> str:
    if rsi <= RSI_OVERSOLD:
        return "과매도"
    if rsi >= RSI_OVERBOUGHT:
        return "과매수"
    return "중립"


def classify_valuation_percentile(pctl: float) -> str:
    if pctl <= VALUATION_UNDERVALUED_PCTL:
        return "저평가"
    if pctl >= VALUATION_OVERVALUED_PCTL:
        return "고평가"
    return "중립"
