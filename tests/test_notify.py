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
