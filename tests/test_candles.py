"""Candle fetching: KIS date-window paging, field mapping, include_today."""

from datetime import date, timedelta

import pytest

from quant import candles


@pytest.fixture(autouse=True)
def _no_page_delay(monkeypatch):
    monkeypatch.setattr(candles, "PAGE_DELAY", 0)


class FakeKisClient:
    """Serves a synthetic daily history through the inquire-daily-
    itemchartprice contract: newest-first rows, <=100 per call, no cursor,
    truncated to the newest rows within [start, end]."""

    def __init__(self, history_dates: list[str]) -> None:
        self.days = sorted(set(history_dates))  # YYYYMMDD
        self.calls: list[tuple[str, str]] = []
        self.adjust_flags: list[bool] = []

    def daily_chart(self, symbol, start, end, *, adjusted=False):
        self.calls.append((start, end))
        self.adjust_flags.append(adjusted)
        window = [d for d in self.days if start <= d <= end]
        rows = list(reversed(window))[:candles.PAGE_SIZE]  # newest first, capped
        return {"output2": [
            {"stck_bsop_date": d, "stck_oprc": "10", "stck_hgpr": "12",
             "stck_lwpr": "9", "stck_clpr": "11", "acml_vol": "100",
             "acml_tr_pbmn": "1100", "flng_cls_code": "00"}
            for d in rows
        ]}


def _hist(n: int, end: date = date(2026, 9, 10)) -> list[str]:
    """n consecutive calendar days ending `end`. The fetcher doesn't model
    the trading calendar, so weekends are fine here."""
    return [(end - timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


def test_kis_maps_output2_to_common_shape():
    client = FakeKisClient(_hist(5))
    out = candles.fetch_kis(client, "102110", 5, today=date(2026, 9, 10))

    assert out[0] == {
        "timestamp": "2026-09-10T00:00:00.000+09:00",
        "openPrice": "10", "highPrice": "12", "lowPrice": "9",
        "closePrice": "11", "volume": "100",
        "tradingValue": "1100", "flngClsCode": "00",
    }


def test_kis_requests_adjusted_prices():
    # 수정주가, to stay interchangeable with the Toss candle series.
    client = FakeKisClient(_hist(5))
    candles.fetch_kis(client, "102110", 5, today=date(2026, 9, 10))
    assert client.adjust_flags == [True]


def test_kis_pages_backwards_past_the_100_row_cap():
    client = FakeKisClient(_hist(250))
    out = candles.fetch_kis(client, "102110", 220, today=date(2026, 9, 10))

    assert len(out) == 220
    assert len(client.calls) >= 3            # 100 + 100 + 20
    dates = [c["timestamp"][:10] for c in out]
    assert dates == sorted(dates, reverse=True)   # newest first
    assert len(dates) == len(set(dates))          # no boundary duplicates


def test_kis_stops_when_history_is_exhausted():
    client = FakeKisClient(_hist(40))
    out = candles.fetch_kis(client, "102110", 220, today=date(2026, 9, 10))

    assert len(out) == 40                     # all there is, no infinite loop


def test_kis_windows_march_backward_without_overlap():
    client = FakeKisClient(_hist(250))
    candles.fetch_kis(client, "102110", 250, today=date(2026, 9, 10))

    for start, end in client.calls:
        assert start < end
    for i in range(1, len(client.calls)):
        # each window ends strictly before the previous window's end
        assert client.calls[i][1] < client.calls[i - 1][1]


def test_get_kis_uses_a_separate_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(candles, "CACHE_DIR", tmp_path)
    client = FakeKisClient(_hist(30))

    candles.get(client, "102110", days=10, today=date(2026, 9, 10),
                source="kis")

    assert (tmp_path / "kis" / "102110.json").exists()
    assert not (tmp_path / "102110.json").exists()


def test_get_kis_excludes_today_unless_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(candles, "CACHE_DIR", tmp_path)
    today = date(2026, 9, 10)

    excl = candles.get(FakeKisClient(_hist(30)), "102110", days=10,
                       today=today, source="kis")
    assert excl[0]["timestamp"][:10] == "2026-09-09"

    incl = candles.get(FakeKisClient(_hist(30)), "102110", days=10,
                       today=today, include_today=True, source="kis")
    assert incl[0]["timestamp"][:10] == "2026-09-10"
