from __future__ import annotations

import sys
import time
from pathlib import Path
from datetime import UTC
from datetime import datetime

import pytest

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from news.models import SentimentSource
from news.models import SourceType
from news.models import UnifiedNewsEvent
from news.normalizer.base import BaseNormalizer
from news.normalizer.base import SCHEMA_VERSION
from news.normalizer.exceptions import NormalizationError
from news.normalizer.finnhub_normalizer import FinnhubNormalizer
from news.normalizer.reddit_normalizer import RedditNormalizer
from news.normalizer.rss_normalizer import RSSNormalizer


class StaticNormalizer(BaseNormalizer):
    SOURCE_NAME = "TEST"

    def __init__(self, event: UnifiedNewsEvent) -> None:
        """Initializes the static test normalizer."""
        super().__init__(watchlist=[])
        self._event = event

    def _normalize_impl(self, raw: dict) -> UnifiedNewsEvent:
        """Returns the prebuilt test event."""
        del raw
        return self._event


def test_finnhub_normalizer_maps_valid_payload() -> None:
    normalizer = FinnhubNormalizer(watchlist=["AAPL"])
    raw = {
        "id": 123,
        "headline": "AAPL beats expectations",
        "summary": "Quarterly revenue increased.",
        "datetime": 1_704_067_200,
        "related": "AAPL,MSFT",
    }

    event = normalizer.normalize(raw)

    assert event.event_id == "123"
    assert event.schema_version == SCHEMA_VERSION
    assert event.source == SourceType.FINNHUB
    assert event.headline == "AAPL beats expectations"
    assert event.body == "Quarterly revenue increased."
    assert event.published_at == datetime.fromtimestamp(1_704_067_200, UTC)
    assert event.symbols == ["AAPL"]
    assert event.raw_sentiment == 0.0
    assert event.sentiment_source == SentimentSource.NONE
    assert event.ingested_at.tzinfo == UTC


def test_rss_normalizer_maps_valid_payload() -> None:
    normalizer = RSSNormalizer(watchlist=["GBP/USD"])
    raw = {
        "link": "https://example.com/story",
        "title": "Sterling firms",
        "summary": "GBP/USD rises after data.",
        "published_parsed": time.gmtime(1_704_067_200),
    }

    event = normalizer.normalize(raw)

    assert event.event_id == "https://example.com/story"
    assert event.source == SourceType.RSS
    assert event.headline == "Sterling firms"
    assert event.body == "GBP/USD rises after data."
    assert event.published_at == datetime.fromtimestamp(1_704_067_200, UTC)
    assert event.symbols == ["GBP/USD"]
    assert event.sentiment_source == SentimentSource.NONE


def test_reddit_normalizer_maps_valid_payload() -> None:
    normalizer = RedditNormalizer(watchlist=["EUR/USD"])
    raw = {
        "id": "abc123",
        "title": "Macro thread",
        "selftext": "EUR/USD looks strong today.",
        "subreddit": "forex",
        "created_utc": 1_704_067_200.0,
    }

    event = normalizer.normalize(raw)

    assert event.event_id == "abc123"
    assert event.source == SourceType.REDDIT
    assert event.headline == "Macro thread"
    assert event.body == "[r/forex] EUR/USD looks strong today."
    assert event.published_at == datetime.fromtimestamp(1_704_067_200.0, UTC)
    assert event.symbols == ["EUR/USD"]
    assert event.sentiment_source == SentimentSource.NONE


def test_naive_datetime_raises_normalization_error() -> None:
    event = UnifiedNewsEvent(
        event_id="naive",
        schema_version=SCHEMA_VERSION,
        source=SourceType.RSS,
        headline="Valid headline",
        body=None,
        published_at=datetime(2024, 1, 1, 0, 0, 0),
        symbols=[],
        raw_sentiment=0.0,
        sentiment_source=SentimentSource.NONE,
        ingested_at=datetime.now(UTC),
    )

    with pytest.raises(NormalizationError, match="TEST normalization failed for field 'published_at'"):
        StaticNormalizer(event).normalize({})


def test_missing_headline_raises_normalization_error() -> None:
    normalizer = FinnhubNormalizer(watchlist=[])
    raw = {
        "id": 123,
        "headline": "",
        "summary": "Quarterly revenue increased.",
        "datetime": 1_704_067_200,
    }

    with pytest.raises(NormalizationError, match="FINNHUB normalization failed for field 'headline'"):
        normalizer.normalize(raw)


def test_symbol_inference_is_not_blocked_by_watchlist() -> None:
    normalizer = RSSNormalizer(watchlist=["EUR/USD"])
    raw = {
        "link": "https://example.com/no-match",
        "title": "Dollar rises after ECB comments",
        "summary": "EUR/USD falls as the euro weakens after central bank remarks.",
        "published_parsed": time.gmtime(1_704_067_200),
    }

    event = normalizer.normalize(raw)

    assert event.symbols == ["EUR/USD"]
