from __future__ import annotations

import calendar
import time
from datetime import UTC
from datetime import datetime

from ..models import SentimentSource
from ..models import SourceType
from ..models import UnifiedNewsEvent
from .base import BaseNormalizer
from .base import SCHEMA_VERSION


class OfficialFeedsNormalizer(BaseNormalizer):
    SOURCE_NAME = SourceType.OFFICIAL.value

    def _normalize_impl(self, raw: dict) -> UnifiedNewsEvent:
        event_id = self._require_string(raw, "link")
        headline = self._require_string(raw, "title")
        body = self._optional_string(raw, "summary")
        published_at = self._published_at(raw)

        return UnifiedNewsEvent(
            event_id=event_id,
            schema_version=SCHEMA_VERSION,
            source=SourceType.OFFICIAL,
            headline=headline,
            body=body,
            published_at=published_at,
            symbols=[],
            raw_sentiment=0.0,
            sentiment_source=SentimentSource.NONE,
            ingested_at=self._now_utc(),
        )

    def _published_at(self, raw: dict) -> datetime:
        published_parsed = raw.get("published_parsed")
        if published_parsed is None:
            return self._now_utc()
        if not isinstance(published_parsed, time.struct_time):
            raise self._error("published_parsed", "must be a time.struct_time value")
        return datetime.fromtimestamp(calendar.timegm(published_parsed), UTC)