from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator
from datetime import UTC
from datetime import datetime
from typing import Any

import feedparser  # type: ignore[import-untyped]
import structlog

from ..interfaces import NewsIngester
from .rss_config import RSSConfig

ENTRY_LINK_KEY = "link"
MAX_DEDUP_ENTRIES = 50_000
EVICTION_PERCENTAGE = 0.10


def utc_now() -> datetime:
    return datetime.now(UTC)


class RSSIngester(NewsIngester):
    def __init__(self, config: RSSConfig) -> None:
        """Initializes the RSS ingester."""
        self.config = config
        self._logger = structlog.get_logger().bind(component=self.__class__.__name__)
        self._seen_entry_links: OrderedDict[str, None] = OrderedDict()
        self._last_successful_ingest_at: datetime | None = None

    async def ingest(self) -> AsyncIterator[dict]:
        """Yields deduplicated raw RSS feed payloads."""
        while True:
            feed_entry_batches = await asyncio.gather(
                *(self._fetch_feed_entries(feed_url) for feed_url in self.config.feed_urls),
            )

            for feed_entries in feed_entry_batches:
                for entry in feed_entries:
                    yield entry

            await asyncio.sleep(self.config.poll_interval_seconds)

    async def _fetch_feed_entries(self, feed_url: str) -> list[dict[str, Any]]:
        try:
            parsed_feed = await asyncio.to_thread(feedparser.parse, feed_url)
        except (OSError, RuntimeError, ValueError) as error:
            self._logger.warning(
                "rss_feed_parse_failed",
                feed_url=feed_url,
                error=str(error),
                exc_info=True,
            )
            return []

        if getattr(parsed_feed, "bozo", False):
            bozo_exception = getattr(parsed_feed, "bozo_exception", None)
            self._logger.warning(
                "rss_feed_bozo",
                feed_url=feed_url,
                error=str(bozo_exception) if bozo_exception is not None else "Unknown feed parse error.",
            )
            return []

        raw_entries = getattr(parsed_feed, "entries", [])
        if not isinstance(raw_entries, list):
            self._logger.warning("rss_feed_entries_invalid", feed_url=feed_url)
            return []

        self._last_successful_ingest_at = utc_now()
        new_entries: list[dict[str, Any]] = []
        for raw_entry in raw_entries[: self.config.max_entries_per_feed]:
            if not isinstance(raw_entry, dict):
                entry = dict(raw_entry)
            else:
                entry = raw_entry

            entry_link = entry.get(ENTRY_LINK_KEY)
            if entry_link is None:
                self._logger.warning("rss_entry_missing_link", feed_url=feed_url)
                continue

            if self._remember_entry_link(str(entry_link)):
                new_entries.append(dict(entry))
        return new_entries

    def _remember_entry_link(self, entry_link: str) -> bool:
        if entry_link in self._seen_entry_links:
            return False

        if len(self._seen_entry_links) >= MAX_DEDUP_ENTRIES:
            self._evict_oldest_entry_links()

        self._seen_entry_links[entry_link] = None
        return True

    def _evict_oldest_entry_links(self) -> None:
        eviction_count = max(1, int(MAX_DEDUP_ENTRIES * EVICTION_PERCENTAGE))
        for _ in range(eviction_count):
            if not self._seen_entry_links:
                break
            self._seen_entry_links.popitem(last=False)

    def is_healthy(self) -> bool:
        """Returns whether the RSS ingester is healthy."""
        if self._last_successful_ingest_at is None:
            return False
        max_age_seconds = 2 * self.config.poll_interval_seconds
        age_seconds = (utc_now() - self._last_successful_ingest_at).total_seconds()
        return age_seconds <= max_age_seconds
