from __future__ import annotations

from datetime import UTC
from datetime import datetime

from ..models import SentimentSource
from ..models import SourceType
from ..models import UnifiedNewsEvent
from .base import BaseNormalizer
from .base import SCHEMA_VERSION


class FinnhubNormalizer(BaseNormalizer):
    SOURCE_NAME = SourceType.FINNHUB.value

    def _normalize_impl(self, raw: dict) -> UnifiedNewsEvent:
        """Builds a unified event from a Finnhub payload."""
        event_id = str(self._require_field(raw, "id"))
        headline = self._require_string(raw, "headline")
        body = self._optional_string(raw, "summary")
        published_at = self._published_at(raw)

        return UnifiedNewsEvent(
            event_id=event_id,
            schema_version=SCHEMA_VERSION,
            source=SourceType.FINNHUB,
            headline=headline,
            body=body,
            published_at=published_at,
            symbols=[],
            raw_sentiment=0.0,
            sentiment_source=SentimentSource.NONE,
            ingested_at=self._now_utc(),
        )

    def _symbol_seed(self, raw: dict) -> str:
        related = raw.get("related")
        if related is None:
            return ""
        return str(related)

    def _published_at(self, raw: dict) -> datetime:
        timestamp = self._require_field(raw, "datetime")
        if not isinstance(timestamp, int | float):
            raise self._error("datetime", "must be a Unix timestamp")
        return datetime.fromtimestamp(timestamp, UTC)
