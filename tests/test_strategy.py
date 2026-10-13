"""Tests for the dual momentum signal (strategy.evaluate)."""

from datetime import date, timedelta
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


def daily(closes: list[str]) -> list[dict]:
    """Newest-first daily candles ending 2026-09-28, oldest close first."""
    end = date(2026, 9, 28)
    n = len(closes)
    return [
        {"timestamp": f"{(end - timedelta(days=n - 1 - i)).isoformat()}"
                      "T00:00:00.000+09:00",
         "closePrice": c}
        for i, c in reversed(list(enumerate(closes)))
    ]


THIRD = Decimal("1") / 3


class TestApplyOverlay:
    @pytest.fixture(autouse=True)
    def _cash(self, monkeypatch):
        monkeypatch.setattr(config, "CASH_SYMBOL", "CASH")

    def test_correction_halves_a_pick_down_over_the_last_month(self):
        # 40 days: flat at 100, then 90 for the last 10 - last month negative.
        data = {"A": daily(["100"] * 30 + ["90"] * 10)}

        out = strategy.apply_overlay(data, {"A": THIRD}, "correction_half")

        # Freed = weight - kept, so the parked share closes the sum exactly.
        assert out == {"A": THIRD / 2, "CASH": THIRD - THIRD / 2}

    def test_correction_keeps_a_pick_flat_or_up_over_the_last_month(self):
        data = {"A": daily(["100"] * 40), "B": daily(["100"] * 30 + ["110"] * 10)}

        out = strategy.apply_overlay(data, {"A": THIRD, "B": THIRD},
                                     "correction_half")

        assert out == {"A": THIRD, "B": THIRD}

    def test_correction_keeps_a_pick_without_a_month_of_history(self):
        data = {"A": daily(["100", "90"])}

        out = strategy.apply_overlay(data, {"A": THIRD}, "correction_half")

        assert out == {"A": THIRD}

    def test_trend_drops_a_pick_below_its_200_day_average(self):
        data = {"A": daily(["100"] * 199 + ["90"])}

        out = strategy.apply_overlay(data, {"A": THIRD}, "trend_200d")

        assert out == {"CASH": THIRD}

    def test_trend_keeps_a_pick_above_its_200_day_average(self):
        data = {"A": daily(["100"] * 199 + ["110"])}

        out = strategy.apply_overlay(data, {"A": THIRD}, "trend_200d")

        assert out == {"A": THIRD}

    def test_trend_drops_a_pick_without_200_days_of_history(self):
        # Same rule as variant_trend_filtered: no average, no hold.
        data = {"A": daily(["110"] * 50)}

        out = strategy.apply_overlay(data, {"A": THIRD}, "trend_200d")

        assert out == {"CASH": THIRD}

    def test_freed_weight_adds_to_already_parked_cash(self):
        data = {"A": daily(["100"] * 30 + ["90"] * 10),
                "B": daily(["100"] * 40)}
        weights = {"A": THIRD, "B": THIRD, "CASH": THIRD}

        out = strategy.apply_overlay(data, weights, "correction_half")

        assert out["CASH"] == THIRD + (THIRD - THIRD / 2)
        assert sum(out.values()) == sum(weights.values())

    def test_unknown_overlay_is_refused(self):
        with pytest.raises(ValueError):
            strategy.apply_overlay({}, {"A": THIRD}, "nope")

    def test_variants_record_correction_half_without_cash(self, monkeypatch):
        monkeypatch.setattr(config, "UNIVERSE",
                            {"A": "a", "B": "b", "CASH": "cash"})
        monkeypatch.setattr(config, "TOP_N", 3)
        # 400 days rising to 200, then falling to 180 over the last 10:
        # still far ahead of cash on 12-1, but a Correction this month.
        a = daily([str(100 + i // 4) for i in range(390)] + ["180"] * 10)
        data = {"A": a, "B": daily(["100"] * 400), "CASH": daily(["100"] * 400)}
        signal = strategy.Signal(weights={"A": THIRD, "CASH": 2 * THIRD},
                                 cash_weight=Decimal("0"),
                                 scores=[strategy.Score("A", "a", Decimal("0.5"))])

        recorded = {v.name: v.weights for v in strategy.variants(data, {}, signal)}

        assert recorded["correction_half"] == {"A": THIRD / 2}
