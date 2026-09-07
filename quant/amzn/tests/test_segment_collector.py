"""Dry-run schema validation for segment_collector.py - no real SEC EDGAR
calls. The synthetic XBRL instance below mirrors the structure confirmed
against Amazon's real 2026-07-31 10-Q instance document (accession
0001018724-26-000026): a quarter context and a YTD context both present
for the same dimensioned concept, which is exactly the ambiguity
_select_context() must resolve correctly.
"""
from quant.amzn.config import AmznConfig
from quant.amzn.segment_collector import parse_filing

SYNTHETIC_10Q_XBRL = """<?xml version="1.0" encoding="utf-8"?>
<xbrl
  xmlns="http://www.xbrl.org/2003/instance"
  xmlns:dei="http://xbrl.sec.gov/dei/2026"
  xmlns:us-gaap="http://fasb.org/us-gaap/2026"
  xmlns:xbrldi="http://xbrl.org/2006/xbrldi">
  <context id="c1">
    <entity><identifier scheme="http://www.sec.gov/CIK">0001018724</identifier></entity>
    <period><startDate>2026-04-01</startDate><endDate>2026-06-30</endDate></period>
  </context>
  <context id="c2">
    <entity><identifier scheme="http://www.sec.gov/CIK">0001018724</identifier></entity>
    <period><startDate>2026-01-01</startDate><endDate>2026-06-30</endDate></period>
  </context>
  <context id="c3">
    <entity>
      <identifier scheme="http://www.sec.gov/CIK">0001018724</identifier>
      <segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">amzn:AmazonWebServicesSegmentMember</xbrldi:explicitMember></segment>
    </entity>
    <period><startDate>2026-04-01</startDate><endDate>2026-06-30</endDate></period>
  </context>
  <context id="c4">
    <entity>
      <identifier scheme="http://www.sec.gov/CIK">0001018724</identifier>
      <segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">amzn:AmazonWebServicesSegmentMember</xbrldi:explicitMember></segment>
    </entity>
    <period><startDate>2026-01-01</startDate><endDate>2026-06-30</endDate></period>
  </context>
  <context id="c5">
    <entity>
      <identifier scheme="http://www.sec.gov/CIK">0001018724</identifier>
      <segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">amzn:AdvertisingServicesMember</xbrldi:explicitMember></segment>
    </entity>
    <period><startDate>2026-04-01</startDate><endDate>2026-06-30</endDate></period>
  </context>
  <dei:DocumentPeriodEndDate contextRef="c1">2026-06-30</dei:DocumentPeriodEndDate>
  <us-gaap:NetCashProvidedByUsedInOperatingActivities contextRef="c1" unitRef="usd" decimals="-6">15000000000</us-gaap:NetCashProvidedByUsedInOperatingActivities>
  <us-gaap:PaymentsToAcquireProductiveAssets contextRef="c1" unitRef="usd" decimals="-6">6000000000</us-gaap:PaymentsToAcquireProductiveAssets>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="c3" unitRef="usd" decimals="-6">42232000000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="c4" unitRef="usd" decimals="-6">79819000000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
  <us-gaap:OperatingIncomeLoss contextRef="c3" unitRef="usd" decimals="-6">10160000000</us-gaap:OperatingIncomeLoss>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="c5" unitRef="usd" decimals="-6">15000000000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
</xbrl>
"""


def _facts_by_metric(rows):
    return {r["metric"]: r for r in rows}


def test_parse_filing_picks_quarter_context_not_ytd():
    config = AmznConfig()
    rows = parse_filing(SYNTHETIC_10Q_XBRL, "10-Q", "2026-07-31",
                         "0001018724-26-000026", config)
    facts = _facts_by_metric(rows)

    # 42.232B (c3, quarter) must win over 79.819B (c4, YTD) for the same
    # AWS-dimensioned concept - this is the core point-in-time-adjacent
    # correctness property (analogous to dart.py not conflating YTD and
    # quarterly figures).
    assert facts["aws_revenue"]["value"] == 42_232_000_000
    assert facts["aws_revenue"]["is_estimable"] == 1
    assert facts["aws_revenue"]["period_end"] == "2026-06-30"


def test_parse_filing_separates_aws_from_advertising_dimension():
    config = AmznConfig()
    rows = parse_filing(SYNTHETIC_10Q_XBRL, "10-Q", "2026-07-31",
                         "0001018724-26-000026", config)
    facts = _facts_by_metric(rows)

    assert facts["advertising_revenue"]["value"] == 15_000_000_000
    assert facts["aws_revenue"]["value"] != facts["advertising_revenue"]["value"]


def test_parse_filing_computes_fcf_from_ocf_and_capex():
    config = AmznConfig()
    rows = parse_filing(SYNTHETIC_10Q_XBRL, "10-Q", "2026-07-31",
                         "0001018724-26-000026", config)
    facts = _facts_by_metric(rows)

    assert facts["ocf"]["value"] == 15_000_000_000
    assert facts["capex"]["value"] == 6_000_000_000
    assert facts["fcf"]["value"] == 9_000_000_000
    assert facts["fcf"]["is_estimable"] == 1


def test_parse_filing_isolates_period_start_per_metric():
    """Regression guard: period_start must come from each metric's own
    resolved context, not a single shared "consolidated" lookup whose
    failure would previously leave every metric's period_start silently
    defaulted to a zero-length period (period_start == period_end).

    Here c1 (ocf/capex's consolidated context) gets an incidental stray
    dimensional member unrelated to segment/product axes, which makes the
    "no dimension at all" filter skip it - ocf/capex become genuinely
    inestimable, but aws_revenue/advertising_revenue (resolved via their
    own c3/c5 contexts) must be unaffected.
    """
    xbrl_with_stray_member_on_c1 = SYNTHETIC_10Q_XBRL.replace(
        '<context id="c1">\n'
        '    <entity><identifier scheme="http://www.sec.gov/CIK">0001018724</identifier></entity>\n'
        '    <period><startDate>2026-04-01</startDate><endDate>2026-06-30</endDate></period>\n'
        '  </context>',
        '<context id="c1">\n'
        '    <entity>\n'
        '      <identifier scheme="http://www.sec.gov/CIK">0001018724</identifier>\n'
        '      <segment><xbrldi:explicitMember dimension="srt:ScenarioAxis">us-gaap:ActualMember</xbrldi:explicitMember></segment>\n'
        '    </entity>\n'
        '    <period><startDate>2026-04-01</startDate><endDate>2026-06-30</endDate></period>\n'
        '  </context>',
    )
    assert xbrl_with_stray_member_on_c1 != SYNTHETIC_10Q_XBRL  # sanity: replacement actually matched

    config = AmznConfig()
    rows = parse_filing(xbrl_with_stray_member_on_c1, "10-Q", "2026-07-31",
                         "0001018724-26-000026", config)
    facts = _facts_by_metric(rows)

    assert facts["ocf"]["is_estimable"] == 0
    assert facts["ocf"]["period_start"] is None
    assert facts["capex"]["is_estimable"] == 0

    # Unaffected - resolved via their own AWS/advertising-dimensioned contexts.
    assert facts["aws_revenue"]["is_estimable"] == 1
    assert facts["aws_revenue"]["period_start"] == "2026-04-01"
    assert facts["advertising_revenue"]["is_estimable"] == 1


def test_parse_filing_missing_concept_is_none_not_zero():
    # Strip the AWS operating-income fact out entirely - the filing simply
    # doesn't tag it this quarter (taxonomy change, e.g.).
    xbrl_without_aws_oi = SYNTHETIC_10Q_XBRL.replace(
        '<us-gaap:OperatingIncomeLoss contextRef="c3" unitRef="usd" decimals="-6">10160000000</us-gaap:OperatingIncomeLoss>',
        "",
    )
    config = AmznConfig()
    rows = parse_filing(xbrl_without_aws_oi, "10-Q", "2026-07-31",
                         "0001018724-26-000026", config)
    facts = _facts_by_metric(rows)

    assert facts["aws_operating_income"]["value"] is None
    assert facts["aws_operating_income"]["is_estimable"] == 0
    # period_start must stay honestly unknown (None), never fall back to a
    # fabricated zero-length period (period_start == period_end).
    assert facts["aws_operating_income"]["period_start"] is None
