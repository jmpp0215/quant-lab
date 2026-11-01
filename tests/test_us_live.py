from datetime import date

import us_live

ROLLING_VTT = """WEBVTT
Kind: captions
Language: ko

00:00:06.399 --> 00:00:15.990 align:start position:0%

안녕하세요<00:00:06.919><c> 여러분</c>

00:00:15.990 --> 00:00:16.000 align:start position:0%
안녕하세요 여러분


00:00:16.000 --> 00:00:19.670 align:start position:0%
안녕하세요 여러분
오늘은<00:00:17.160><c> 10월</c><00:00:17.400><c> 28일</c>

00:00:19.670 --> 00:00:19.680 align:start position:0%
오늘은 10월 28일


00:00:19.680 --> 00:00:21.630 align:start position:0%
오늘은 10월 28일
엔비디아

01:02:03.000 --> 01:02:04.000 align:start position:0%
엔비디아
급등
"""


class TestParseVtt:
    def test_keeps_each_rolling_line_once_with_its_second(self):
        assert us_live.parse_vtt(ROLLING_VTT) == [
            (6, "안녕하세요 여러분"), (16, "오늘은 10월 28일"), (19, "엔비디아"), (3723, "급등")]

    def test_unescapes_entities_and_drops_speaker_marks(self):
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n&gt;&gt; 금리 &amp; 환율<00:00:01.500><c> 동결</c>\n"
        assert us_live.parse_vtt(vtt) == [(1, "금리 & 환율 동결")]

    def test_empty_and_header_only(self):
        assert us_live.parse_vtt("") == []
        assert us_live.parse_vtt("WEBVTT\nKind: captions\n") == []


class TestChunks:
    def test_splits_by_hour_with_minute_marks(self):
        lines = [(0, "a"), (30, "b"), (61, "c"), (3600, "d"), (7300, "e")]
        assert us_live.chunks(lines) == ["[0:00:00] a b \n[0:01:01] c", "[1:00:00] d", "[2:01:40] e"]

    def test_empty(self):
        assert us_live.chunks([]) == []


class TestFindStream:
    def entry(self, title, live="was_live", duration=34000, id_="x"):
        return {"id": id_, "title": title, "live_status": live, "duration": duration}

    def test_picks_the_finished_regular_stream_for_the_day(self):
        entries = [self.entry("[생방송]  PCE ｜ 아마존", live="is_live", duration=None, id_="live"),
                   self.entry("[특별 방송] 삼성전자 - 실적발표", duration=4978, id_="special"),
                   self.entry("[26년 10월 28일 수]  연준 금리 결정", id_="fomc"),
                   self.entry("[26년 10월 27일 화]  엔비디아", id_="older")]
        assert us_live.find_stream(entries, date(2026, 10, 28))["id"] == "fomc"
        assert us_live.find_stream(entries, date(2026, 10, 27))["id"] == "older"

    def test_none_while_still_live_or_too_short(self):
        entries = [self.entry("[26년 10월 29일 목]  PCE", live="is_live", duration=None),
                   self.entry("[26년 10월 29일 목]  짧은 방송", duration=3600)]
        assert us_live.find_stream(entries, date(2026, 10, 29)) is None

    def test_title_date(self):
        assert us_live.title_date("[26년 1월 2일 금] x") == date(2026, 1, 2)
        assert us_live.title_date("[특별 방송] x") is None


class TestClean:
    def test_parses_time_and_filters_tickers(self):
        out = us_live.clean({"headline": ["a", "b", "c", "d"], "issues": [
            {"time": "1:02:03", "title": "t", "detail": "d", "tickers": ["nvda", "엔비디아", "TOOLONGX", "BRK.B"]}]})
        assert out["headline"] == ["a", "b", "c"]
        assert out["issues"][0] == {"time": "1:02:03", "sec": 3723, "title": "t", "detail": "d",
                                    "tickers": ["NVDA", "BRK.B"]}

    def test_bad_time_keeps_issue_without_link_and_caps(self):
        issues = [{"time": "오후 3시", "title": "x" * 500, "detail": "", "tickers": []}] * 20
        out = us_live.clean({"headline": [], "issues": issues})
        assert len(out["issues"]) == us_live.MAX_ISSUES
        assert out["issues"][0]["sec"] is None and out["issues"][0]["time"] == ""
        assert len(out["issues"][0]["title"]) == 120

    def test_sorts_by_time_with_unknown_times_last(self):
        issues = [{"time": t, "title": t, "detail": "", "tickers": []} for t in ("0:30:00", "?", "0:05:01")]
        assert [r["title"] for r in us_live.clean({"headline": [], "issues": issues})["issues"]] == [
            "0:05:01", "0:30:00", "?"]
