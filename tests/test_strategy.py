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

        assert set(signal.weights) == {"A"}
        assert signal.cash_weight == Decimal("1") - Decimal("1") / 3

    def test_cash_proxy_itself_is_never_selected(self):
        data = {
            "A": candles("100", "90"),
            "B": candles("100", "90"),
            "C": candles("100", "90"),
            "CASH": candles("100", "103"),
        }

        signal = strategy.evaluate(data, {})

        assert signal.weights == {}
        assert signal.cash_weight == Decimal("1")
