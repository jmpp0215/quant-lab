"""Tests for the record-only disclosure bad-news flags."""

import pytest

from quant.stock_factors import disclosures


class TestClassifyTitle:
    @pytest.mark.parametrize("title", [
        "횡령ㆍ배임혐의발생", "[기재정정]상장폐지사유발생", "관리종목지정",
        "주요사항보고서(감자결정)", "회생절차개시신청",
    ])
    def test_severe_titles(self, title):
        assert disclosures.classify_title(title) == "severe"

    @pytest.mark.parametrize("title", [
        "주요사항보고서(유상증자결정)", "[기재정정]주요사항보고서(전환사채권발행결정)",
        "감사보고서제출", "타인에대한채무보증결정", "풍문또는보도에대한해명",
        "주요사항보고서",  # no subtype: could be anything
    ])
    def test_review_titles(self, title):
        assert disclosures.classify_title(title) == "review"

    @pytest.mark.parametrize("title", [
        "임원ㆍ주요주주특정증권등소유상황보고서", "주식등의대량보유상황보고서",
        "단일판매ㆍ공급계약체결", "현금ㆍ현물배당결정", "[기재정정]반기보고서",
        "타법인주식및출자증권취득결정",
    ])
    def test_neutral_titles(self, title):
        assert disclosures.classify_title(title) == "neutral"

    def test_severe_wins_over_review(self):
        # 감자 (severe) inside a major-events report (review on its own).
        assert disclosures.classify_title("주요사항보고서(감자결정)") == "severe"


class TestWindow:
    def test_sixty_days_ending_the_day_before(self):
        assert disclosures.window("2026-10-16") == ("20260817", "20261015")

    def test_crosses_a_year_boundary(self):
        assert disclosures.window("2027-01-13") == ("20261114", "20270112")


def test_body_text_strips_tags_and_truncates():
    raw = "<A><B>유상증자   결정</B>\n<C>1. 신주</C></A>"
    assert disclosures.body_text(raw) == "유상증자 결정 1. 신주"
    assert disclosures.body_text(raw, limit=4) == "유상증자"


def test_prompt_lists_every_filing_and_the_categories():
    p = disclosures.build_prompt("003060", "에이프로젠", [
        {"rcept_no": "1", "rcept_dt": "20260903", "report_nm": "주요사항보고서(유상증자결정)",
         "body": "신주 12,706,481주"},
        {"rcept_no": "2", "rcept_dt": "20260904", "report_nm": "감사보고서제출", "body": "적정"},
    ])
    assert "[1] 20260903 주요사항보고서(유상증자결정)" in p and "[2] 20260904" in p
    assert "에이프로젠(003060)" in p
    assert all(c in p for c in disclosures.CATEGORIES)


class TestParseReply:
    def test_reads_a_fenced_array(self):
        text = ('```json\n[{"rcept_no": "1", "severe": true, "category": "대규모 희석", '
                '"reason": "78% 희석"}]\n```')
        assert disclosures.parse_reply(text, ["1"]) == {
            "1": {"severe": True, "category": "대규모 희석", "reason": "78% 희석"}}

    def test_ignores_unknown_and_malformed_items(self):
        text = ('[{"rcept_no": "9", "severe": true}, {"rcept_no": "1", "severe": "yes"},'
                ' "junk", {"rcept_no": "2", "severe": false, "category": "?"}]')
        out = disclosures.parse_reply(text, ["1", "2"])
        # "9" was not asked, "1" has no boolean - both left unjudged.
        assert out == {"2": {"severe": False, "category": "해당없음", "reason": ""}}

    def test_unknown_category_on_a_severe_item_is_not_guessed(self):
        out = disclosures.parse_reply('[{"rcept_no": "1", "severe": true, "category": "x"}]', ["1"])
        assert out["1"]["category"] is None

    def test_no_array_raises(self):
        with pytest.raises(ValueError):
            disclosures.parse_reply("I cannot help", ["1"])


def row(title_class, llm_severe=None):
    return {"title_class": title_class, "llm_severe": llm_severe}


class TestSymbolFlags:
    def test_neutral_only_flags_nothing(self):
        assert disclosures.symbol_flags([row("neutral")]) == {"R1": False, "R2": False, "L": False}

    def test_severe_title_flags_all_three(self):
        assert disclosures.symbol_flags([row("severe"), row("review", False)]) == {
            "R1": True, "R2": True, "L": True}

    def test_review_judged_harmless_flags_only_r2(self):
        assert disclosures.symbol_flags([row("review", False)]) == {
            "R1": False, "R2": True, "L": False}

    def test_review_judged_severe_flags_r2_and_l(self):
        assert disclosures.symbol_flags([row("review", False), row("review", True)]) == {
            "R1": False, "R2": True, "L": True}

    def test_unjudged_review_leaves_l_unknown(self):
        assert disclosures.symbol_flags([row("review", False), row("review", None)])["L"] is None
