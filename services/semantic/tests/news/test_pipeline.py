from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from news.analyzer.config import AnalyzerConfig
from news.analyzer.context_tagger import ContextTagger
from news.analyzer.sentiment_analyzer import SentimentAnalyzer
from news.interfaces import NewsIngester
from news.models import SentimentSignal
from news.models import SentimentSource
from news.models import SourceType
from news.models import UnifiedNewsEvent
from news.normalizer.finnhub_normalizer import FinnhubNormalizer
from news.normalizer.rss_normalizer import RSSNormalizer
from news.pipeline import NewsPipeline
from news.queue.in_memory_queue import InMemoryNewsQueue


class MockFinnhubIngester(NewsIngester):
    source_type = SourceType.FINNHUB

    async def ingest(self) -> AsyncIterator[dict]:
        """Yields mock Finnhub payloads."""
        for index in range(3):
            yield {
                "id": index,
                "headline": f"EUR/USD headline {index}",
                "summary": "EUR/USD moves higher after GDP surprise.",
                "datetime": 1_704_067_200,
                "related": "EUR/USD",
            }

    def is_healthy(self) -> bool:
        """Returns the mock Finnhub ingester health status."""
        return True


class MockRSSIngester(NewsIngester):
    source_type = SourceType.RSS

    async def ingest(self) -> AsyncIterator[dict]:
        """Yields mock RSS payloads."""
        for index in range(3):
            yield {
                "link": f"https://example.com/story-{index}",
                "title": f"GBP/USD title {index}",
                "summary": "GBP/USD reacts to inflation data.",
                "published_parsed": None,
            }

    def is_healthy(self) -> bool:
        """Returns the mock RSS ingester health status."""
        return True


class MockBadFinnhubIngester(NewsIngester):
    source_type = SourceType.FINNHUB

    async def ingest(self) -> AsyncIterator[dict]:
        """Yields one bad and one good Finnhub payload."""
        yield {
            "id": "bad",
            "summary": "Missing headline should dead-letter.",
            "datetime": 1_704_067_200,
            "related": "EUR/USD",
        }
        yield {
            "id": "good",
            "headline": "EUR/USD rebounds after CPI print",
            "summary": "EUR/USD moves on macro data.",
            "datetime": 1_704_067_200,
            "related": "EUR/USD",
        }

    def is_healthy(self) -> bool:
        """Returns the mock bad Finnhub ingester health status."""
        return True


class MockSentimentEnricher:
    async def enrich(self, event: UnifiedNewsEvent) -> UnifiedNewsEvent:
        """Returns a fixed enriched event for tests."""
        return replace(event, raw_sentiment=0.7, sentiment_source=SentimentSource.FINBERT)


@pytest.mark.asyncio
async def test_pipeline_processes_signals_and_stops_cleanly() -> None:
    normalizers = {
        SourceType.FINNHUB: FinnhubNormalizer(["EUR/USD", "GBP/USD"]),
        SourceType.RSS: RSSNormalizer(["EUR/USD", "GBP/USD"]),
    }
    output_queue: asyncio.Queue[SentimentSignal] = asyncio.Queue()
    dead_letter: asyncio.Queue[dict] = asyncio.Queue()
    pipeline = NewsPipeline(
        ingesters=[MockFinnhubIngester(), MockRSSIngester()],
        normalizers=normalizers,
        enricher=MockSentimentEnricher(),
        queue=InMemoryNewsQueue(),
        analyzer=SentimentAnalyzer(AnalyzerConfig(), ContextTagger()),
        output_queue=output_queue,
        dead_letter=dead_letter,
    )

    run_task = asyncio.create_task(pipeline.run())

    signal = await asyncio.wait_for(output_queue.get(), timeout=5)
    assert isinstance(signal, SentimentSignal)

    await asyncio.wait_for(pipeline.stop(), timeout=5)
    await asyncio.wait_for(run_task, timeout=5)


@pytest.mark.asyncio
async def test_pipeline_routes_normalization_errors_to_dead_letter() -> None:
    output_queue: asyncio.Queue[SentimentSignal] = asyncio.Queue()
    dead_letter: asyncio.Queue[dict] = asyncio.Queue()
    pipeline = NewsPipeline(
        ingesters=[MockBadFinnhubIngester()],
        normalizers={SourceType.FINNHUB: FinnhubNormalizer(["EUR/USD"])},
        enricher=MockSentimentEnricher(),
        queue=InMemoryNewsQueue(),
        analyzer=SentimentAnalyzer(AnalyzerConfig(), ContextTagger()),
        output_queue=output_queue,
        dead_letter=dead_letter,
    )

    run_task = asyncio.create_task(pipeline.run())

    dead_letter_item = await asyncio.wait_for(dead_letter.get(), timeout=5)
    signal = await asyncio.wait_for(output_queue.get(), timeout=5)

    assert dead_letter_item["id"] == "bad"
    assert signal.symbol == "EUR/USD"

    await asyncio.wait_for(pipeline.stop(), timeout=5)
    await asyncio.wait_for(run_task, timeout=5)
