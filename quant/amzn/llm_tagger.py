"""LLM-based headline -> event-tag classification, used only for
unstructured-to-structured conversion (never for signal generation - see
quant/amzn/README.md). Called once per headline at collection time by
news_collector.py; the result is frozen into amzn_news_events and never
recomputed by a later read.

Requires the `anthropic` package (not a project-wide dependency yet - only
this module needs it) and ANTHROPIC_API_KEY in .env. Import is deferred to
call time so importing news_collector.py (e.g. for its dry-run test) never
requires either.
"""
import logging
import os

from quant.amzn.config import AmznConfig

log = logging.getLogger("amzn.llm_tagger")

PROMPT_TEMPLATE = """You are tagging a financial news headline about Amazon (AMZN) with exactly one \
category from this fixed list: {tags}.

Headline: "{headline}"

Reply with only the category name, nothing else. If none clearly fits, reply with the last category \
in the list ("기타" or equivalent catch-all)."""


def tag_headline(headline: str, config: AmznConfig) -> str | None:
    """Returns one of config.event_tags, or None on any failure (API error,
    empty response, or a reply that doesn't match a known tag) - never a
    guessed default, per the project's honest-failure convention.
    """
    try:
        import anthropic
    except ImportError:
        log.error("anthropic package not installed - run `pip install anthropic`")
        return None

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set in .env")
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=config.llm_model,
            max_tokens=20,
            messages=[{
                "role": "user",
                "content": PROMPT_TEMPLATE.format(
                    tags=", ".join(config.event_tags), headline=headline),
            }],
        )
        # Sonnet 5 can return a leading ThinkingBlock before the TextBlock
        # (no `.text` attribute) - find the actual text block by type
        # rather than assuming content[0] is it.
        text_blocks = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        if not text_blocks:
            log.error("LLM response for %r had no text block: %r", headline[:80], response.content)
            return None
        tag = text_blocks[0].strip()
    except Exception as e:
        log.error("LLM tagging failed for %r: %s", headline[:80], e)
        return None

    if tag not in config.event_tags:
        log.warning("LLM returned unrecognized tag %r for %r", tag, headline[:80])
        return None
    return tag
