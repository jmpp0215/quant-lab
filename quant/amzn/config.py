"""Config for the AMZN data-collection layer.

Mirrors quant/pead/config.py's PeadConfig pattern: a dataclass whose
get_hash() lets downstream tables key results by which collection/parsing
choices produced them, so a later change to (say) which XBRL dimension
member is treated as "AWS" doesn't silently blend with older rows.
"""
import hashlib
import json
from dataclasses import dataclass


@dataclass
class AmznConfig:
    # quant/kis_client.py's overseas price-detail endpoint (해외주식 현재가상세,
    # tr_id HHDFS76200200) and exchange code.
    price_tr_id: str = "HHDFS76200200"
    exchange: str = "NAS"

    # Which XBRL taxonomy year's tag/dimension names segment_collector.py
    # assumes (Amazon's own extension namespace changes per filing year,
    # e.g. xmlns:amzn="http://www.amazon.com/20260630" - the *concept* and
    # *member* local names have been stable across recent years, but this
    # is the field to bump if a filing ever renames them and the parser
    # needs updating to match).
    segment_taxonomy_version: str = "amzn-2026"
    segment_axis: str = "us-gaap:StatementBusinessSegmentsAxis"
    aws_member: str = "amzn:AmazonWebServicesSegmentMember"
    advertising_axis: str = "srt:ProductOrServiceAxis"
    advertising_member: str = "amzn:AdvertisingServicesMember"

    # news_collector.py's LLM event-tagging step.
    llm_model: str = "claude-sonnet-5"
    llm_prompt_version: str = "v1"
    event_tags: tuple = ("실적발표", "규제", "AWS계약", "경쟁사동향", "기타")

    def get_hash(self) -> str:
        """Config combination's stable hash, for tracking which rows came
        from which collection/parsing config (same pattern as
        PeadConfig.get_hash())."""
        config_dict = {**self.__dict__, "event_tags": list(self.event_tags)}
        config_str = json.dumps(config_dict, sort_keys=True)
        return hashlib.sha256(config_str.encode("utf-8")).hexdigest()
