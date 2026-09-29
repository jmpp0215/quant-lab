"""Tests for the dual momentum signal (strategy.evaluate)."""

from decimal import Decimal

import pytest

from quant import config, strategy


def candles(start: str, end: str) -> list[dict]:
    """Newest-first candles giving a 12-month return of end/start - 1."""
    return [
        {"timestamp": "2026-09-28T00:00:00.000+09:00", "closePrice": end},
        {"timestamp": "2025-09-26T00:00:00.000+09:00", "closePrice": start},
    ]


def test_cash_symbol_is_in_universe():
    # evaluate only scores UNIVERSE symbols; a cash proxy outside it leaves
    # the hurdle at 0% without any error.
    assert config.CASH_SYMBOL in config.UNIVERSE


class TestEvaluateHurdle:
    @pytest.fixture(autouse=True)
    def _small_universe(self, monkeypatch):
        monkeypatch.setattr(config, "UNIVERSE",
                            {"A": "a", "B": "b", "C": "c", "CASH": "cash"})
        monkeypatch.setattr(config, "CASH_SYMBOL", "CASH")
        monkeypatch.setattr(config, "TOP_N", 3)
        monkeypatch.setattr(config, "LOOKBACK_MONTHS", 12)
        monkeypatch.setattr(config, "SKIP_MONTHS", 0)

    def test_symbols_must_beat_cash_not_zero(self):
        data = {
            "A": candles("100", "110"),     # +10%
            "B": candles("100", "102"),     # +2%: positive, but below cash
            "C": candles("100", "95"),      # -5%
            "CASH": candles("100", "103"),  # +3%
        }

        signal = strategy.evaluate(data, {})

        # Only A beats cash; the two empty slots are parked in the cash proxy.
        assert signal.weights == {"A": Decimal("1") / 3,
                                  "CASH": Decimal("1") - Decimal("1") / 3}
        assert signal.cash_weight == Decimal("0")

    def test_nothing_beats_cash_parks_everything_in_cash_proxy(self):
        data = {
            "A": candles("100", "90"),
            "B": candles("100", "90"),
            "C": candles("100", "90"),
            "CASH": candles("100", "103"),
        }

        signal = strategy.evaluate(data, {})

        assert signal.weights == {"CASH": Decimal("1")}
        assert signal.cash_weight == Decimal("0")

    def test_full_selection_leaves_no_cash_position(self):
        # 3 x 1/3 leaves only a rounding tail - not a real cash allocation.
        data = {
            "A": candles("100", "120"),
            "B": candles("100", "115"),
            "C": candles("100", "110"),
            "CASH": candles("100", "103"),
        }

        signal = strategy.evaluate(data, {})

        assert set(signal.weights) == {"A", "B", "C"}
        assert signal.cash_weight == Decimal("0")

    def test_variants_ignore_parked_cash(self):
        # The parked cash-proxy weight is residual, not a pick: re-weighting
        # schemes must not treat it as a fourth holding (inverse-vol would
        # pour most of the book into a near-zero-vol money-market fund).
        data = {
            "A": candles("100", "110"),
            "B": candles("100", "102"),
            "C": candles("100", "95"),
            "CASH": candles("100", "103"),
        }
        signal = strategy.evaluate(data, {})

        for v in strategy.variants(data, {}, signal):
            assert "CASH" not in v.weights, v.name
