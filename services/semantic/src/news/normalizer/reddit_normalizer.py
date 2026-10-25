from __future__ import annotations

from datetime import UTC
from datetime import datetime

from ..models import SentimentSource
from ..models import SourceType
from ..models import UnifiedNewsEvent
from .base import BaseNormalizer
from .base import SCHEMA_VERSION


class RedditNormalizer(BaseNormalizer):
    SOURCE_NAME = SourceType.REDDIT.value

    def _normalize_impl(self, raw: dict) -> UnifiedNewsEvent:
        """Builds a unified event from a Reddit payload."""
        event_id = self._require_string(raw, "id")
        headline = self._require_string(raw, "title")
        subreddit = self._require_string(raw, "subreddit")
        selftext = self._optional_string(raw, "selftext") or ""
        body = f"[r/{subreddit}] {selftext}".strip()
        published_at = self._published_at(raw)

        return UnifiedNewsEvent(
            event_id=event_id,
            schema_version=SCHEMA_VERSION,
            source=SourceType.REDDIT,
            headline=headline,
            body=body,
            published_at=published_at,
            symbols=[],
            raw_sentiment=0.0,
            sentiment_source=SentimentSource.NONE,
            ingested_at=self._now_utc(),
        )

    def _published_at(self, raw: dict) -> datetime:
        created_utc = self._require_field(raw, "created_utc")
        if not isinstance(created_utc, int | float):
            raise self._error("created_utc", "must be a Unix timestamp")
        return datetime.fromtimestamp(created_utc, UTC)
