from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

DEFAULT_POLL_INTERVAL_SECONDS = 300
DEFAULT_MAX_ENTRIES_PER_FEED = 20
ENV_PREFIX = "OFFICIAL_FEEDS_"


def default_feed_urls() -> list[str]:
    return [
        "https://www.federalreserve.gov/feeds/press_monetary.xml",
        "https://www.federalreserve.gov/feeds/speeches.xml",
        "https://www.ecb.europa.eu/rss/press.html",
        "https://www.ecb.europa.eu/rss/speeches.html",
        "https://www.bankofengland.co.uk/rss/news",
    ]


class OfficialFeedsConfig(BaseSettings):
    feed_urls: list[str] = Field(default_factory=default_feed_urls)
    poll_interval_seconds: int = Field(default=DEFAULT_POLL_INTERVAL_SECONDS, ge=0)
    max_entries_per_feed: int = Field(default=DEFAULT_MAX_ENTRIES_PER_FEED, ge=1)

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")