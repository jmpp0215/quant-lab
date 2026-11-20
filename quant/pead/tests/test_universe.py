import pandas as pd

from quant.pead.universe import kospi_history_sources


def test_backfill_includes_delisted_kospi_issues():
    class Listings:
        @staticmethod
        def StockListing(market, *dates):
            if market == "KOSPI":
                return pd.DataFrame({"Code": ["000001"]})
            assert (market, dates) == ("KRX-DELISTING", ("2020-01-01", "2026-11-20"))
            return pd.DataFrame({"Market": ["KOSPI", "KOSDAQ", "유가증권시장"],
                                 "Symbol": ["KR7000002000", "000003", "000004"]})

    assert kospi_history_sources(Listings, "2020-01-01", "2026-11-20") == {
        "000001": "000001", "000002": "KRX-DELISTING:000002", "000004": "KRX-DELISTING:000004"}
