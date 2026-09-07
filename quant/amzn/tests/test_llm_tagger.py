"""No real Anthropic API calls - the client is monkeypatched. Regression
coverage for the ThinkingBlock/TextBlock content-ordering bug: Sonnet 5 can
return a leading block without a `.text` attribute before the actual
answer, and content[0].text blindly assumed the first block was text.
"""
import anthropic
import pytest

from quant.amzn.config import AmznConfig
from quant.amzn.llm_tagger import tag_headline


class _FakeBlock:
    def __init__(self, type_: str, text: str | None = None):
        self.type = type_
        if text is not None:
            self.text = text


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeMessages:
    def __init__(self, content):
        self._content = content

    def create(self, **kwargs):
        return _FakeResponse(self._content)


class _FakeAnthropicClient:
    def __init__(self, content):
        self.messages = _FakeMessages(content)


@pytest.fixture
def config():
    return AmznConfig()


def test_tag_headline_skips_leading_thinking_block(monkeypatch, config):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    content = [_FakeBlock("thinking", text=None), _FakeBlock("text", text="AWS계약")]
    monkeypatch.setattr(anthropic, "Anthropic",
                         lambda api_key: _FakeAnthropicClient(content))

    tag = tag_headline("Amazon signs new AWS cloud deal", config)

    assert tag == "AWS계약"


def test_tag_headline_plain_text_response_still_works(monkeypatch, config):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    content = [_FakeBlock("text", text="실적발표")]
    monkeypatch.setattr(anthropic, "Anthropic",
                         lambda api_key: _FakeAnthropicClient(content))

    tag = tag_headline("Amazon reports Q2 earnings", config)

    assert tag == "실적발표"


def test_tag_headline_returns_none_for_unrecognized_tag(monkeypatch, config):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    content = [_FakeBlock("text", text="NotARealTag")]
    monkeypatch.setattr(anthropic, "Anthropic",
                         lambda api_key: _FakeAnthropicClient(content))

    tag = tag_headline("some headline", config)

    assert tag is None


def test_tag_headline_returns_none_without_api_key(monkeypatch, config):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    tag = tag_headline("some headline", config)

    assert tag is None
