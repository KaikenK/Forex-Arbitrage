from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

DEFAULT_POLL_INTERVAL_SECONDS = 60
DEFAULT_MAX_ENTRIES_PER_FEED = 20
ENV_PREFIX = "RSS_"


def default_feed_urls() -> list[str]:
    return [
        # Global
        "https://feeds.reuters.com/reuters/businessNews",
        "http://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "https://www.ft.com/rss/home/uk",
        "https://www.wsj.com/xml/rss/3_7014.xml",
        # India / INR focused
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "https://www.livemint.com/rss/markets",
        "https://www.business-standard.com/rss/markets-106.rss",
        "https://www.moneycontrol.com/rss/markets.xml",
        "https://www.ndtv.com/rss/business/latest.xml",
    ]


class RSSConfig(BaseSettings):
    feed_urls: list[str] = Field(default_factory=default_feed_urls)
    poll_interval_seconds: int = Field(default=DEFAULT_POLL_INTERVAL_SECONDS, ge=0)
    max_entries_per_feed: int = Field(default=DEFAULT_MAX_ENTRIES_PER_FEED, ge=1)

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")
