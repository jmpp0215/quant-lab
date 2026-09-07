"""Dry-run schema validation for news_collector.py - no real KIS calls.
Mock response shapes mirror KIS's official news_title.py / brknews_title.py
samples' COLUMN_MAPPINGs (verified 2026-09-07 against
koreainvestment/open-trading-api).
"""
from quant.amzn.news_collector import (
    _published_at,
    parse_brknews_title_rows,
    parse_news_title_rows,
)

MOCK_NEWS_TITLE_OUTBLOCK1 = [
    {
        "news_key": "20260907090512AMZN01",
        "data_dt": "20260907",
        "data_tm": "090512",
        "class_cd": "01",
        "class_name": "실적",
        "source": "Reuters",
        "nation_cd": "US",
        "exchange_cd": "NAS",
        "symb": "AMZN",
        "symb_name": "Amazon.com Inc",
        "title": "Amazon Q2 AWS revenue beats estimates",
    },
    {
        "news_key": "",  # malformed row - no key
        "data_dt": "20260907",
        "data_tm": "091000",
        "title": "some other headline",
    },
]

MOCK_BRKNEWS_OUTPUT = [
    {
        "cntt_usiq_srno": "998877",
        "news_ofer_entp_code": "0",
        "data_dt": "20260907",
        "data_tm": "101530",
        "hts_pbnt_titl_cntt": "Amazon announces new AWS data center in Seoul",
        "news_lrdv_code": "02",
        "dorg": "Bloomberg",
        "iscd1": "AMZN",
    },
]


def test_published_at_combines_date_and_time():
    assert _published_at("20260907", "090512") == "2026-09-07T09:05:12"


def test_published_at_handles_short_time():
    assert _published_at("20260907", "9") == "2026-09-07T00:00:09"


def test_parse_news_title_rows_maps_fields_and_skips_malformed():
    rows = parse_news_title_rows(MOCK_NEWS_TITLE_OUTBLOCK1, "AMZN", "2026-09-07T12:00:00")

    assert len(rows) == 1  # the empty-news_key row is skipped, not fabricated
    row = rows[0]
    assert row["source_api"] == "news_title"
    assert row["news_key"] == "20260907090512AMZN01"
    assert row["headline"] == "Amazon Q2 AWS revenue beats estimates"
    assert row["published_at"] == "2026-09-07T09:05:12"
    assert row["source"] == "Reuters"


def test_parse_brknews_title_rows_maps_fields():
    rows = parse_brknews_title_rows(MOCK_BRKNEWS_OUTPUT, "AMZN", "2026-09-07T12:00:00")

    assert len(rows) == 1
    row = rows[0]
    assert row["source_api"] == "brknews_title"
    assert row["news_key"] == "998877"
    assert row["headline"] == "Amazon announces new AWS data center in Seoul"
    assert row["source"] == "Bloomberg"
