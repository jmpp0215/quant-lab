"""Dry-run schema validation for price_collector.py - no real KIS calls.
The mock response shape below mirrors KIS's official price_detail.py
sample's COLUMN_MAPPING (verified 2026-09-07 against
koreainvestment/open-trading-api).
"""
from quant.amzn.price_collector import parse_price_detail

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
