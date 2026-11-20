import pandas as pd
import pytest

from quant.stock_factors.scripts.pbr_cost_sweep import simulate


def test_reverse_split_needs_a_verified_share_factor():
    days = ["20260105", "20260106"]
    prices = pd.DataFrame({"A": [100, 1010]}, index=pd.to_datetime(days))
    args = (days, prices, lambda _: ["A"], 0, 0, "full")
    with pytest.raises(ValueError, match="20260106"):
        simulate(*args)
    gross, _, _ = simulate(*args, share_factors={(days[1], "A"): 0.1})
    assert gross == pytest.approx(0.01)
