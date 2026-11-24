from datetime import date, timedelta

import pytest

from quant.stock_factors.rights_offering import Offering
from quant.stock_factors.scripts import rights_offerings


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    monkeypatch.setattr(rights_offerings.dart, "_get_api_key", lambda: "test")


class TestScanWindows:
    def test_covers_the_lookback_in_windows_of_at_most_step_days(self):
        as_of = date(2026, 11, 24)
        windows = rights_offerings.scan_windows(as_of, lookback=200, step=90)
        assert windows[0][0] == (as_of - timedelta(days=200)).strftime("%Y%m%d")
        assert windows[-1][1] == "20261124"
        for (_, e), (nb, _) in zip(windows, windows[1:], strict=False):
            assert date.fromisoformat(f"{nb[:4]}-{nb[4:6]}-{nb[6:]}") == \
                date.fromisoformat(f"{e[:4]}-{e[4:6]}-{e[6:]}") + timedelta(days=1)
        for b, e in windows:
            span = (date(int(e[:4]), int(e[4:6]), int(e[6:]))
                    - date(int(b[:4]), int(b[4:6]), int(b[6:]))).days
            assert span <= 89


class TestListMarketFilings:
    def test_no_filings_is_empty(self, monkeypatch):
        monkeypatch.setattr(rights_offerings.dart, "_rate_limit_and_retry",
                            lambda url, params: {"list": []})
        assert rights_offerings.list_market_filings("20260101", "20260331") == []

    def test_api_error_raises(self, monkeypatch):
        monkeypatch.setattr(rights_offerings.dart, "_rate_limit_and_retry",
                            lambda url, params: {"status": "020", "message": "limit"})
        with pytest.raises(RuntimeError):
            rights_offerings.list_market_filings("20260101", "20260331")


class TestFilingFacts:
    @pytest.fixture
    def bodies(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rights_offerings, "FILING_CACHE", tmp_path / "f.json")
        bodies, fetched = {}, []

        def fake_body(no):
            fetched.append(no)
            return bodies[no]

        monkeypatch.setattr(rights_offerings, "full_body", fake_body)
        return bodies, fetched

    def test_falls_back_to_an_older_version_and_caches(self, bodies):
        texts, fetched = bodies
        texts |= {"20261007000004": "정정사항 발행가 거래소 신고의무 12. 납입일 - 13.",
                  "20260728000478": "거래소 신고의무 12. 납입일 2026년 10월 15일"}
        o = Offering("210980", "20260728", ("20261007000004", "20260728000478"))
        assert rights_offerings.filing_facts([o]) == ({"20261007000004": "20261015"}, {})
        assert rights_offerings.filing_facts([o]) == ({"20261007000004": "20261015"}, {})
        assert fetched == ["20261007000004", "20260728000478"]

    def test_withdrawal_in_the_latest_correction(self, bodies):
        texts, _ = bodies
        texts["20260521000766"] = ("정정사항 유상증자 결정 유상증자 철회 "
                                   "주요사항보고서 / 거래소 신고의무 사항 9. 납입일 -")
        o = Offering("089860", "20250228", ("20260521000766",))
        assert rights_offerings.filing_facts([o]) == ({}, {"20260521000766": "20260521"})

    def test_none_when_no_version_states_a_date(self, bodies):
        texts, _ = bodies
        texts["20260101000001"] = ""
        o = Offering("A", "20260101", ("20260101000001",))
        assert rights_offerings.filing_facts([o]) == ({"20260101000001": None}, {})
