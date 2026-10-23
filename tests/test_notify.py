"""Tests for AppleScript string escaping in desktop notifications."""

from quant.notify import _quote


class TestQuote:
    def test_wraps_plain_text_in_double_quotes(self):
        assert _quote("hello") == '"hello"'

    def test_escapes_embedded_double_quotes(self):
        # An unescaped quote here would terminate the AppleScript string
        # literal early, corrupting the script rather than the message.
        assert _quote('say "hi"') == '"say \\"hi\\""'

    def test_escapes_backslashes_before_quotes_are_escaped(self):
        # Must escape backslashes first: escaping quotes before
        # backslashes would double-escape the backslashes just inserted.
        assert _quote("a\\b") == '"a\\\\b"'

    def test_round_trips_a_reconcile_drift_message(self):
        # The kind of message check_account_health actually sends -
        # dict repr contains both a symbol string with no special chars
        # and integers, nothing exotic, but exercises the real call shape.
        msg = "tranche books disagree with the account: {'102110': -3}"
        quoted = _quote(msg)
        assert quoted.startswith('"') and quoted.endswith('"')
        assert "102110" in quoted


class TestPhoneNotifications:
    def test_imessage_script_quotes_text_and_recipient(self):
        from quant.notify import imessage_script
        s = imessage_script("me@example.com", 'drift "x"')
        assert 'send "drift \\"x\\"" to participant "me@example.com" of s' in s
        assert "service type = iMessage" in s

    def test_notify_sends_imessage_only_when_configured(self, monkeypatch):
        from quant import notify as n
        calls = []
        monkeypatch.setattr(n.subprocess, "run", lambda args, **kw: calls.append(args[-1]))
        monkeypatch.delenv("IMESSAGE_TO", raising=False)
        n.notify("t", "m")
        assert len(calls) == 1

        monkeypatch.setenv("IMESSAGE_TO", "+821000000000")
        n.notify("t", "m")
        assert len(calls) == 3 and "[t] m" in calls[-1]

    def test_ping_success_and_fail_urls(self, monkeypatch):
        from quant import notify as n
        urls = []
        monkeypatch.setattr(n.requests, "get", lambda url, timeout: urls.append(url))
        monkeypatch.delenv("HEALTHCHECK_PING_URL", raising=False)
        n.ping()
        assert urls == []

        monkeypatch.setenv("HEALTHCHECK_PING_URL", "https://hc-ping.com/abc/")
        n.ping()
        n.ping(ok=False)
        assert urls == ["https://hc-ping.com/abc/", "https://hc-ping.com/abc/fail"]


class TestPhoneText:
    def test_daily_problems_become_short_korean_lines(self):
        from quant.notify import phone_text
        msg = ("daily.py: [toss-bot] tranche books disagree with the account: {'025530': 74, "
               "'363280': 46, '001380': -160}; unrecorded transfer? 2026-09-04 +1,104,887 KRW, "
               "2026-10-16 +283,485 KRW - record or dismiss in the tracker / [kis-isa] api error: boom")
        assert phone_text(msg) == ("daily 점검 결과\n"
                                   "• 토스: 장부와 실제 잔고가 3종목 다름 (리밸런싱 전 정리 필요), "
                                   "기록 안 된 입출금 의심 2건 → 트래커에서 확인\n"
                                   "• ISA: 증권사 API 오류")

    def test_side_jobs_and_fetch_failure(self):
        from quant.notify import phone_text
        assert phone_text("daily.py: [backup] disk full / [upcoming] x") == (
            "daily 점검 결과\n• DB 백업 실패\n• 일정·미리보기 계산 실패")
        assert phone_text("daily.py: [price history] 403") == (
            "daily 점검 결과\n• 위기 재생용 가격 이력 갱신 실패")
        assert phone_text("daily.py: [news risk] refusal") == "daily 점검 결과\n• 위험 뉴스 요약 실패"
        assert phone_text("daily.py 실패 (candle/calendar fetch): ConnectionError(...)").startswith(
            "daily 실패: 시세")

    def test_other_messages_pass_through_truncated(self):
        from quant.notify import PHONE_MAX, phone_text
        assert phone_text("[kis-isa] 오늘 트랜치 1 리밸런싱 예정") == "[kis-isa] 오늘 트랜치 1 리밸런싱 예정"
        assert len(phone_text("가" * 1000)) == PHONE_MAX
