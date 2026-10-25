from __future__ import annotations

import sys
from pathlib import Path
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import cast

import pytest

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

import news.analyzer.sentiment_analyzer as sentiment_analyzer_module
from news.analyzer.config import AnalyzerConfig
from news.analyzer.context_tagger import ContextTagger
from news.analyzer.sentiment_analyzer import SentimentAnalyzer
from news.models import ContextTag
from news.models import SentimentSource
from news.models import SourceType
from news.models import UnifiedNewsEvent


def make_event(
    *,
    source: SourceType,
    headline: str,
    published_at: datetime,
    symbols: list[str],
    raw_sentiment: float,
) -> UnifiedNewsEvent:
    return UnifiedNewsEvent(
        event_id="event-1",
        schema_version=1,
        source=source,
        headline=headline,
        body="Body text",
        published_at=published_at,
        symbols=symbols,
        raw_sentiment=raw_sentiment,
        sentiment_source=SentimentSource.NONE,
        ingested_at=published_at,
    )


@pytest.mark.asyncio
async def test_sentiment_analyzer_produces_normal_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    current_time = datetime(2026, 3, 23, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(sentiment_analyzer_module, "utc_now", lambda: current_time)

    analyzer = SentimentAnalyzer(AnalyzerConfig(), ContextTagger())
    event = make_event(
        source=SourceType.FINNHUB,
        headline="Fed rate decision boosts USD",
        published_at=current_time,
        symbols=["EUR/USD"],
        raw_sentiment=0.8,
    )

    signals = await analyzer.analyze(event)

    assert len(signals) == 1
    assert signals[0].event_id == "event-1"
    assert signals[0].symbol == "EUR/USD"
    assert signals[0].score == pytest.approx(0.8)
    assert signals[0].confidence == pytest.approx(0.85 * 1.0 * 0.8)
    assert signals[0].context_tag == ContextTag.CENTRAL_BANK
    assert signals[0].produced_at == current_time


@pytest.mark.asyncio
async def test_sentiment_analyzer_applies_recency_penalty(monkeypatch: pytest.MonkeyPatch) -> None:
    current_time = datetime(2026, 3, 23, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(sentiment_analyzer_module, "utc_now", lambda: current_time)

    analyzer = SentimentAnalyzer(AnalyzerConfig(recency_window_seconds=60), ContextTagger())
    event = make_event(
        source=SourceType.RSS,
        headline="GDP growth surprises markets",
        published_at=current_time - timedelta(seconds=60),
        symbols=["GBP/USD"],
        raw_sentiment=0.6,
    )

    signals = await analyzer.analyze(event)

    assert len(signals) == 1
    assert signals[0].confidence == pytest.approx(0.75 * 0.5 * 0.6)
    assert signals[0].context_tag == ContextTag.MACRO


@pytest.mark.asyncio
async def test_sentiment_analyzer_returns_empty_for_missing_symbols() -> None:
    analyzer = SentimentAnalyzer(AnalyzerConfig(), ContextTagger())
    event = make_event(
        source=SourceType.REDDIT,
        headline="Discussion thread",
        published_at=datetime(2026, 3, 23, 12, 0, tzinfo=UTC),
        symbols=[],
        raw_sentiment=0.4,
    )

    signals = await analyzer.analyze(event)

    assert signals == []


@pytest.mark.asyncio
async def test_sentiment_analyzer_defaults_unknown_source_weight(monkeypatch: pytest.MonkeyPatch) -> None:
    current_time = datetime(2026, 3, 23, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(sentiment_analyzer_module, "utc_now", lambda: current_time)

    analyzer = SentimentAnalyzer(AnalyzerConfig(), ContextTagger())
    event = make_event(
        source=cast(SourceType, "newsletter"),
        headline="Revenue outlook improves",
        published_at=current_time,
        symbols=["AAPL"],
        raw_sentiment=0.4,
    )

    signals = await analyzer.analyze(event)

    assert len(signals) == 1
    assert signals[0].confidence == pytest.approx(0.5 * 1.0 * 0.4)
    assert signals[0].context_tag == ContextTag.EARNINGS


def test_context_tagger_matches_tags_and_defaults_other() -> None:
    tagger = ContextTagger()

    assert tagger.tag("Powell hints at another rate decision") == ContextTag.CENTRAL_BANK
    assert tagger.tag("Geopolitical conflict disrupts trade flows") == ContextTag.GEOPOLITICAL
    assert tagger.tag("Earnings beat lifts revenue guidance") == ContextTag.EARNINGS
    assert tagger.tag("CPI and GDP data surprise analysts") == ContextTag.MACRO
    assert tagger.tag("Company unveils new product design") == ContextTag.OTHER
