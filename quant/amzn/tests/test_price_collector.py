"""Dry-run schema validation for price_collector.py - no real KIS calls.
The mock response shape below mirrors KIS's official price_detail.py
sample's COLUMN_MAPPING (verified 2026-09-07 against
koreainvestment/open-trading-api).
"""
import sqlite3
from datetime import datetime

from quant.amzn import storage
from quant.amzn.config import AmznConfig
from quant.amzn.price_collector import (
    KST,
    collect_and_store,
    parse_holding,
    parse_price_detail,
)

MOCK_PRICE_DETAIL_OUTPUT = {
    "rsym": "DNASAMZN",
    "last": "215.4300",
    "base": "213.1000",
    "perx": "38.1234",
    "pbrx": "7.8900",
    "epsx": "5.6500",
    "bpsx": "27.3100",
    "tomv": "2265432100000",
    "shar": "10515000000",
    "tvol": "42315678",
    "curr": "USD",
}


def test_parse_price_detail_maps_known_fields():
    row = parse_price_detail(MOCK_PRICE_DETAIL_OUTPUT)

    assert row["last_price"] == 215.43
    assert row["per"] == 38.1234
    assert row["pbr"] == 7.89
    assert row["eps"] == 5.65
    assert row["bps"] == 27.31
    assert row["market_cap"] == 2265432100000.0
    assert row["shares_outstanding"] == 10515000000.0
    assert row["volume"] == 42315678.0
    assert row["currency"] == "USD"


def test_parse_price_detail_missing_field_is_none_not_zero():
    output = dict(MOCK_PRICE_DETAIL_OUTPUT)
    output["perx"] = ""  # KIS returns empty string, not absent key, for N/A ratios

    row = parse_price_detail(output)

    assert row["per"] is None  # honest failure, never a silently-defaulted 0


def test_parse_price_detail_junk_value_is_none():
    output = dict(MOCK_PRICE_DETAIL_OUTPUT)
    output["pbrx"] = "-"  # KIS sometimes returns a bare dash for N/A

    row = parse_price_detail(output)

    assert row["pbr"] is None


MOCK_HOLDING_ROW = {
    "pdno": "AMZN",
    "ccld_qty_smtl1": "5",
    "ovrs_now_pric1": "220.00",
    "avg_unpr3": "210.00",
    "evlu_pfls_rt1": "4.76",
    "evlu_pfls_amt2": "50.00",
}


def test_parse_holding_returns_matching_symbol():
    holding = parse_holding([MOCK_HOLDING_ROW], "AMZN")

    assert holding["qty"] == 5
    assert holding["avg_price"] == 210.0
    assert holding["current_price"] == 220.0
    assert holding["unrealized_pnl_pct"] == 4.76
    assert holding["unrealized_pnl_usd"] == 50.0


def test_parse_holding_symbol_absent_returns_none():
    assert parse_holding([], "AMZN") is None
    assert parse_holding([{**MOCK_HOLDING_ROW, "pdno": "AAPL"}], "AMZN") is None


def test_parse_holding_zero_qty_returns_none():
    row = {**MOCK_HOLDING_ROW, "ccld_qty_smtl1": "0"}
    assert parse_holding([row], "AMZN") is None


def test_parse_holding_falls_back_to_computed_pnl():
    row = dict(MOCK_HOLDING_ROW)
    del row["evlu_pfls_rt1"]
    del row["evlu_pfls_amt2"]

    holding = parse_holding([row], "AMZN")

    assert holding["unrealized_pnl_pct"] == (220.0 - 210.0) / 210.0 * 100
    assert holding["unrealized_pnl_usd"] == (220.0 - 210.0) * 5


class FakeClient:
    def __init__(self, output):
        self._output = output
        self.calls = 0

    def price_detail_overseas(self, symbol, exchange="NAS"):
        self.calls += 1
        return {"output": self._output}


def test_collect_and_store_writes_row_first_call(tmp_path, monkeypatch):
    db_path = tmp_path / "test_amzn.db"
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    client = FakeClient(MOCK_PRICE_DETAIL_OUTPUT)
    config = AmznConfig()
    now = datetime(2026, 9, 7, 9, 0, tzinfo=KST)

    row = collect_and_store(client, "AMZN", config, now=now)

    assert row is not None
    assert row["date"] == "20260907"
    assert client.calls == 1


def test_collect_and_store_skips_same_day_rerun(tmp_path, monkeypatch):
    db_path = tmp_path / "test_amzn.db"
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    client = FakeClient(MOCK_PRICE_DETAIL_OUTPUT)
    config = AmznConfig()
    now = datetime(2026, 9, 7, 9, 0, tzinfo=KST)

    first = collect_and_store(client, "AMZN", config, now=now)
    second = collect_and_store(client, "AMZN", config, now=now)

    assert first is not None
    assert second is None
    assert client.calls == 1

    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT count(*) FROM amzn_price_daily WHERE symbol='AMZN'"
        ).fetchone()[0]
    assert count == 1


def test_collect_and_store_collects_again_next_day(tmp_path, monkeypatch):
    db_path = tmp_path / "test_amzn.db"
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    client = FakeClient(MOCK_PRICE_DETAIL_OUTPUT)
    config = AmznConfig()

    day1 = collect_and_store(client, "AMZN", config,
                              now=datetime(2026, 9, 7, 9, 0, tzinfo=KST))
    day2 = collect_and_store(client, "AMZN", config,
                              now=datetime(2026, 9, 8, 9, 0, tzinfo=KST))

    assert day1 is not None
    assert day2 is not None
    assert client.calls == 2
