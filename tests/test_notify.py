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
