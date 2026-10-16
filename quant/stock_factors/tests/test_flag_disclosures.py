import pytest

from quant.stock_factors.scripts import flag_disclosures


def _fake_dart(pages):
    calls = iter(pages)
    return lambda url, params: next(calls)


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    # list_filings reads the key before each call; tests must not need .env
    monkeypatch.setattr(flag_disclosures.dart, "_get_api_key", lambda: "test")


class TestListFilings:
    def test_no_filings_in_range_is_empty_not_an_error(self, monkeypatch):
        # dart._rate_limit_and_retry turns status 013 into {"list": []} with
        # no status key - this is what aborted the 2026-10-16 run.
        monkeypatch.setattr(flag_disclosures.dart, "_rate_limit_and_retry",
                            _fake_dart([{"list": []}]))
        assert flag_disclosures.list_filings("00145686", "20260817", "20261015") == []

    def test_pages_are_concatenated(self, monkeypatch):
        monkeypatch.setattr(flag_disclosures.dart, "_rate_limit_and_retry", _fake_dart([
            {"status": "000", "total_page": 2, "list": [{"rcept_no": "1"}]},
            {"status": "000", "total_page": 2, "list": [{"rcept_no": "2"}]},
        ]))
        out = flag_disclosures.list_filings("x", "20260101", "20260301")
        assert [f["rcept_no"] for f in out] == ["1", "2"]

    def test_other_api_errors_still_raise(self, monkeypatch):
        monkeypatch.setattr(flag_disclosures.dart, "_rate_limit_and_retry",
                            _fake_dart([{"status": "100", "message": "bad key"}]))
        with pytest.raises(RuntimeError, match="status 100"):
            flag_disclosures.list_filings("x", "20260101", "20260301")
