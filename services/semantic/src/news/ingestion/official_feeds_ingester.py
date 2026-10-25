from __future__ import annotations

import asyncio
from typing import Any

import feedparser  # type: ignore[import-untyped]

from ..models import SourceType
from .official_feeds_config import OfficialFeedsConfig
from .rss_ingester import ENTRY_LINK_KEY
from .rss_ingester import RSSIngester
from .rss_ingester import utc_now


class OfficialFeedsIngester(RSSIngester):
    source_type = SourceType.OFFICIAL

    def __init__(self, config: OfficialFeedsConfig) -> None:
        super().__init__(config)

    async def _fetch_feed_entries(self, feed_url: str) -> list[dict[str, Any]]:
        try:
            parsed_feed = await asyncio.to_thread(feedparser.parse, feed_url)
        except (OSError, RuntimeError, ValueError) as error:
            self._logger.warning(
                "official_feed_parse_failed",
                feed_url=feed_url,
                error=str(error),
                exc_info=True,
            )
            return []

        raw_entries = getattr(parsed_feed, "entries", [])
        if not isinstance(raw_entries, list):
            self._logger.warning("official_feed_entries_invalid", feed_url=feed_url)
            return []

        if getattr(parsed_feed, "bozo", False):
            bozo_exception = getattr(parsed_feed, "bozo_exception", None)
            if not raw_entries:
                self._logger.warning(
                    "official_feed_bozo_empty",
                    feed_url=feed_url,
                    error=str(bozo_exception) if bozo_exception is not None else "Unknown feed parse error.",
                )
                return []
            self._logger.warning(
                "official_feed_bozo_tolerated",
                feed_url=feed_url,
                error=str(bozo_exception) if bozo_exception is not None else "Unknown feed parse warning.",
                entry_count=len(raw_entries),
            )

        self._last_successful_ingest_at = utc_now()
        new_entries: list[dict[str, Any]] = []
        for raw_entry in raw_entries[: self.config.max_entries_per_feed]:
            entry = dict(raw_entry) if not isinstance(raw_entry, dict) else raw_entry
            entry_link = entry.get(ENTRY_LINK_KEY)
            if entry_link is None:
                self._logger.warning("official_feed_entry_missing_link", feed_url=feed_url)
                continue
            if self._remember_entry_link(str(entry_link)):
                new_entries.append(dict(entry))
        return new_entries
