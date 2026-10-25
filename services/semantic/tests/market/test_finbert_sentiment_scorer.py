from __future__ import annotations

import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch, PropertyMock

import pytest

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from market.finbert_sentiment_scorer import FinBERTSentimentScorer
from market.schemas import AnalysisContext, ArticleInput
from news.models import SentimentSource, SourceType, UnifiedNewsEvent


def _make_context(headline: str, body: str | None = None) -> AnalysisContext:
    event = UnifiedNewsEvent(
        event_id="test-1",
        schema_version=1,
        source=SourceType.RSS,
        headline=headline,
        body=body,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        symbols=["USD/INR"],
        raw_sentiment=0.0,
        sentiment_source=SentimentSource.NONE,
        ingested_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return AnalysisContext(
        article=ArticleInput(article_text=headline + (" " + body if body else "")),
        event=event,
    )


@pytest.mark.asyncio
async def test_finbert_positive_score() -> None:
    """FinBERT returns a positive score and FINBERT source."""
    scorer = FinBERTSentimentScorer()
    scorer._enricher = AsyncMock()
    scorer._enricher.score = AsyncMock(return_value=0.75)
    scorer._enricher._load_failed = False

    context = _make_context("RBI holds rates steady, rupee strengthens")
    result = await scorer.score(context)

    assert result.score == pytest.approx(0.75)
    assert result.confidence >= 0.3
    assert result.confidence <= 0.95
    assert result.source == SentimentSource.FINBERT
    assert any("FinBERT" in d.label for d in result.key_drivers)


@pytest.mark.asyncio
async def test_finbert_negative_score() -> None:
    """FinBERT returns a negative score correctly clamped."""
    scorer = FinBERTSentimentScorer()
    scorer._enricher = AsyncMock()
    scorer._enricher.score = AsyncMock(return_value=-0.6)
    scorer._enricher._load_failed = False

    context = _make_context("Fed raises rates aggressively, dollar surges")
    result = await scorer.score(context)

    assert result.score == pytest.approx(-0.6)
    assert result.source == SentimentSource.FINBERT
    assert any("NEGATIVE" in d.label for d in result.key_drivers)


@pytest.mark.asyncio
async def test_finbert_neutral_score() -> None:
    """Near-zero scores produce NEUTRAL label with low confidence."""
    scorer = FinBERTSentimentScorer()
    scorer._enricher = AsyncMock()
    scorer._enricher.score = AsyncMock(return_value=0.02)
    scorer._enricher._load_failed = False

    context = _make_context("Market opens flat today")
    result = await scorer.score(context)

    assert result.score == pytest.approx(0.02)
    assert result.confidence == 0.3
    assert any("NEUTRAL" in d.label for d in result.key_drivers)


@pytest.mark.asyncio
async def test_finbert_fallback_on_load_failure() -> None:
    """When FinBERT model fails to load, falls back to keyword scorer."""
    scorer = FinBERTSentimentScorer()
    scorer._enricher = AsyncMock()
    scorer._enricher.score = AsyncMock(return_value=0.0)
    scorer._enricher._load_failed = True

    context = _make_context("Fed raises interest rates sharply", "Dollar strengthens on rate hike.")
    result = await scorer.score(context)

    # Keyword fallback should still produce a result
    assert -1.0 <= result.score <= 1.0
    assert result.confidence >= 0.0


@pytest.mark.asyncio
async def test_finbert_empty_text_uses_fallback() -> None:
    """Empty article text triggers keyword fallback."""
    scorer = FinBERTSentimentScorer()
    event = UnifiedNewsEvent(
        event_id="test-empty",
        schema_version=1,
        source=SourceType.RSS,
        headline="",
        body=None,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        symbols=[],
        raw_sentiment=0.0,
        sentiment_source=SentimentSource.NONE,
        ingested_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    context = AnalysisContext(
        article=ArticleInput(article_text=""),
        event=event,
    )

    result = await scorer.score(context)
    assert -1.0 <= result.score <= 1.0


@pytest.mark.asyncio
async def test_finbert_score_clamped_to_range() -> None:
    """Scores outside [-1, 1] are clamped."""
    scorer = FinBERTSentimentScorer()
    scorer._enricher = AsyncMock()
    scorer._enricher.score = AsyncMock(return_value=1.5)
    scorer._enricher._load_failed = False

    context = _make_context("Extreme bullish event")
    result = await scorer.score(context)

    assert result.score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_finbert_blends_keyword_drivers() -> None:
    """FinBERT result includes blended keyword drivers when available."""
    scorer = FinBERTSentimentScorer()
    scorer._enricher = AsyncMock()
    scorer._enricher.score = AsyncMock(return_value=0.5)
    scorer._enricher._load_failed = False

    context = _make_context(
        "RBI rate decision boosts rupee",
        "The central bank held rates steady amid inflation concerns.",
    )
    result = await scorer.score(context)

    # Should have FinBERT driver plus up to 2 keyword drivers
    assert len(result.key_drivers) >= 1
    assert result.key_drivers[0].label.startswith("FinBERT:")
