from quant.stock_factors import rights_offering as ro
from quant.stock_factors.rights_offering import Offering


def _f(sym, dt, no, nm):
    return {"stock_code": sym, "rcept_dt": dt, "rcept_no": no, "report_nm": nm}


DECISION = "주요사항보고서(유상증자결정)"


class TestTitles:
    def test_decision_and_its_corrections(self):
        assert ro.is_decision(DECISION)
        assert ro.is_decision("[기재정정]주요사항보고서(유상증자결정)")
        assert ro.is_decision("[첨부정정]주요사항보고서(유상증자결정)")

    def test_bonus_issue_is_not_a_rights_offering(self):
        assert not ro.is_decision("주요사항보고서(무상증자결정)")
        assert not ro.is_decision("증권신고서(지분증권)")

    def test_withdrawal(self):
        assert ro.is_withdrawal("유상증자결정 철회")
        assert not ro.is_withdrawal("자기주식취득결정 철회")


class TestGroupOfferings:
    def test_corrections_join_the_original_newest_first(self):
        out = ro.group_offerings([
            _f("210980", "20261007", "3", "[기재정정]" + DECISION),
            _f("210980", "20260728", "1", DECISION),
            _f("210980", "20260831", "2", "[기재정정]" + DECISION),
        ])
        assert out == [Offering("210980", "20260728", ("3", "2", "1"))]
        assert out[0].latest_rcept_no == "3"

    def test_correction_without_original_starts_at_its_own_date(self):
        out = ro.group_offerings([_f("A", "20260105", "9", "[기재정정]" + DECISION)])
        assert out == [Offering("A", "20260105", ("9",))]

    def test_second_original_is_a_new_offering(self):
        out = ro.group_offerings([
            _f("A", "20260101", "1", DECISION),
            _f("A", "20260601", "2", DECISION),
        ])
        assert [o.announced for o in out] == ["20260101", "20260601"]

    def test_withdrawal_closes_the_open_offering(self):
        out = ro.group_offerings([
            _f("A", "20260101", "1", DECISION),
            _f("A", "20260201", "2", "유상증자결정 철회"),
        ])
        assert out == [Offering("A", "20260101", ("1",), withdrawn="20260201")]

    def test_correction_after_withdrawal_starts_a_new_offering(self):
        out = ro.group_offerings([
            _f("A", "20260101", "1", DECISION),
            _f("A", "20260201", "2", "유상증자결정 철회"),
            _f("A", "20260301", "3", "[기재정정]" + DECISION),
        ])
        assert [o.announced for o in out] == ["20260101", "20260301"]
        assert out[1].withdrawn is None

    def test_rows_without_stock_code_and_other_titles_are_ignored(self):
        assert ro.group_offerings([
            _f("", "20260101", "1", DECISION),
            _f("A", "20260101", "2", "주요사항보고서(무상증자결정)"),
        ]) == []


class TestParsePaymentDate:
    def test_shareholder_allotment_form(self):
        body = "11. 청약예정일 구주주 시작일 2026년 10월 12일 12. 납입일 2026년 10월 15일 13. 실권주"
        assert ro.parse_payment_date(body) == "20261015"

    def test_third_party_form(self):
        body = "8. 제3자배정에 대한 정관의 근거 제9조 9. 납입일 2026.04.17 10. 신주의 배당기산일"
        assert ro.parse_payment_date(body) == "20260417"

    def test_prose_mentions_are_not_the_field(self):
        body = "청약증거금은 주금납입일에 주금으로 대체하며, 청약일로부터 납입일까지의 이자는"
        assert ro.parse_payment_date(body) is None

    def test_blank_field(self):
        assert ro.parse_payment_date("12. 납입일 - 13. 실권주 처리계획 -") is None

    def test_correction_table_old_date_loses_to_the_form(self):
        body = ("정정사항 12. 납입일 2026년 10월 10일 2026년 10월 15일 일정 변경 "
                "유상증자 결정 ... 12. 납입일 2026년 10월 15일 13. 실권주")
        assert ro.parse_payment_date(body) == "20261015"


class TestWithdrawal:
    FORM = " 주요사항보고서 / 거래소 신고의무 사항 ..."

    def test_correction_table_withdrawal(self):
        head = ro.correction_head("정정사항 정정전 정정후 유상증자 결정 유상증자 철회" + self.FORM)
        assert ro.says_withdrawn(head)

    def test_other_wordings_seen_on_dart(self):
        for text in ("부득이하게 금번 유상증자를 철회하고", "유상증자 결정 철회",
                     "전 항목 유상증자 철회에 따른 정정 - 유상증자 발행 결정 철회"):
            assert ro.says_withdrawn(ro.correction_head(text + self.FORM)), text

    def test_payment_delay_that_mentions_cheolhoe_is_not_a_withdrawal(self):
        head = ro.correction_head("납입일을 연기하기로 결정하였습니다. 본 건은 투자 의사 "
                                  "철회에 따른 사항은 아니며" + self.FORM)
        assert not ro.says_withdrawn(head)

    def test_withdrawal_mentioned_only_inside_the_form(self):
        assert not ro.says_withdrawn(ro.correction_head(self.FORM + " 유상증자 철회 시 청약금 반환"))

    def test_no_form_header(self):
        assert ro.correction_head("유상증자 철회") == ""


class TestReleaseDate:
    def test_first_report_with_period_after_payment(self):
        reports = [("20260814", "20260630"), ("20261116", "20260930"), ("20270316", "20261231")]
        assert ro.release_date("20260815", reports) == "20261116"

    def test_period_ending_on_payment_day_does_not_count(self):
        # new shares take effect the day after payment
        assert ro.release_date("20260630", [("20260814", "20260630")]) is None

    def test_none_yet(self):
        assert ro.release_date("20261015", [("20260814", "20260630")]) is None


class TestBanned:
    SKDND = Offering("210980", "20260728", ("x",))
    H1 = ("20260814", "20260630")
    ANNUAL = ("20270316", "20261231")

    def test_sk_dnd_first_buy_is_banned(self):
        out = ro.banned([self.SKDND], {"x": "20261015"}, {"210980": [self.H1]}, "20260914")
        assert "210980" in out

    def test_still_banned_after_payment_until_a_report_reflects_it(self):
        assert "210980" in ro.banned([self.SKDND], {"x": "20261015"},
                                     {"210980": [self.H1]}, "20261124")

    def test_released_on_the_reflecting_report_date(self):
        reports = {"210980": [self.H1, self.ANNUAL]}
        assert "210980" in ro.banned([self.SKDND], {"x": "20261015"}, reports, "20270315")
        assert ro.banned([self.SKDND], {"x": "20261015"}, reports, "20270316") == {}

    def test_not_banned_before_the_decision(self):
        assert ro.banned([self.SKDND], {"x": "20261015"}, {}, "20260727") == {}

    def test_withdrawal_lifts_the_ban_from_its_date(self):
        o = Offering("A", "20260101", ("x",), withdrawn="20260201")
        assert "A" in ro.banned([o], {"x": "20260301"}, {}, "20260131")
        assert ro.banned([o], {"x": "20260301"}, {}, "20260201") == {}

    def test_unknown_payment_date_stays_banned(self):
        out = ro.banned([self.SKDND], {"x": None},
                        {"210980": [self.H1, self.ANNUAL]}, "20271231")
        assert "납입일 미확인" in out["210980"]

    def test_size_and_allotment_do_not_matter(self):
        # a small third-party placement paid 06-29 is banned until the H1 report
        o = Offering("B", "20260602", ("y",))
        assert "B" in ro.banned([o], {"y": "20260629"}, {"B": [self.H1]}, "20260813")
        assert ro.banned([o], {"y": "20260629"}, {"B": [self.H1]}, "20260814") == {}

    def test_an_unreleased_earlier_offering_keeps_the_ban(self):
        old = Offering("A", "20260101", ("o",))
        new = Offering("A", "20260601", ("n",))
        out = ro.banned([old, new], {"o": None, "n": "20260610"},
                        {"A": [("20260814", "20260630")]}, "20260901")
        assert "A" in out
