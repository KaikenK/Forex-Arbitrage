from __future__ import annotations

import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from market.finbert_sentiment_scorer import FinBERTSentimentScorer
from market.schemas import AnalysisContext, ArticleInput
from news.enricher.finbert_enricher import FinBERTEnricher, label_probabilities
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


def _scorer(probabilities) -> FinBERTSentimentScorer:
    enricher = AsyncMock()
    enricher.probabilities = AsyncMock(return_value=probabilities)
    return FinBERTSentimentScorer(enricher=enricher)


@pytest.mark.asyncio
async def test_score_is_expected_polarity_and_confidence_is_one_minus_neutral() -> None:
    result = await _scorer((0.70, 0.10, 0.20)).score(_make_context("RBI holds rates steady"))
    assert result.score == pytest.approx(0.60)
    assert result.confidence == pytest.approx(0.80)
    assert result.source == SentimentSource.FINBERT
    assert result.key_drivers[0].label == "FinBERT: POSITIVE"


@pytest.mark.asyncio
async def test_a_neutral_call_keeps_its_lean_and_has_low_confidence() -> None:
    """Top label NEUTRAL used to give exactly 0; the polar probabilities still carry a lean."""
    result = await _scorer((0.05, 0.25, 0.70)).score(_make_context("Markets await the Fed decision"))
    assert result.score == pytest.approx(-0.20)
    assert result.confidence == pytest.approx(0.30)
    assert result.key_drivers[0].label == "FinBERT: NEGATIVE"


@pytest.mark.asyncio
async def test_confidence_has_a_floor() -> None:
    result = await _scorer((0.01, 0.01, 0.98)).score(_make_context("Market opens flat today"))
    assert result.confidence == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_domain_override_flips_the_expected_polarity() -> None:
    """'rupee weakens' forces bearish: the polarity keeps its size and takes the override's sign."""
    result = await _scorer((0.60, 0.20, 0.20)).score(_make_context("Rupee weakens past 88 as oil climbs"))
    assert result.score == pytest.approx(-0.40)


@pytest.mark.asyncio
async def test_unavailable_model_falls_back_to_keywords() -> None:
    result = await _scorer(None).score(_make_context("Fed raises interest rates sharply", "Dollar strengthens."))
    assert result.source != SentimentSource.FINBERT
    assert -1.0 <= result.score <= 1.0


@pytest.mark.asyncio
async def test_empty_text_uses_fallback() -> None:
    event = UnifiedNewsEvent(
        event_id="test-empty", schema_version=1, source=SourceType.RSS, headline="", body=None,
        published_at=datetime(2026, 1, 1, tzinfo=UTC), symbols=[], raw_sentiment=0.0,
        sentiment_source=SentimentSource.NONE, ingested_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    scorer = _scorer((1.0, 0.0, 0.0))
    result = await scorer.score(AnalysisContext(article=ArticleInput(article_text=""), event=event))
    assert -1.0 <= result.score <= 1.0
    scorer._enricher.probabilities.assert_not_awaited()


def test_label_probabilities_reads_flat_and_nested_results() -> None:
    flat = [{"label": "positive", "score": 0.7}, {"label": "negative", "score": 0.1},
            {"label": "neutral", "score": 0.2}]
    assert label_probabilities(flat) == (0.7, 0.1, 0.2)
    assert label_probabilities([flat]) == (0.7, 0.1, 0.2)
    assert label_probabilities([{"label": "positive", "score": 0.9}]) is None       # top label only
    assert label_probabilities("bad") is None


class _FakePipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return [{"label": "positive", "score": 0.5}, {"label": "negative", "score": 0.3},
                {"label": "neutral", "score": 0.2}]


@pytest.mark.asyncio
async def test_enricher_truncates_caches_and_scores_expected_polarity() -> None:
    enricher = FinBERTEnricher(cache_size=2)
    pipeline = _FakePipeline()
    enricher._pipeline = pipeline
    assert await enricher.score(" Dollar steady ") == pytest.approx(0.2)
    assert await enricher.probabilities("Dollar steady") == (0.5, 0.3, 0.2)      # cache hit, same text
    assert len(pipeline.calls) == 1
    assert pipeline.calls[0][1] == {"top_k": None, "truncation": True, "max_length": 128}
    await enricher.probabilities("a")
    await enricher.probabilities("b")                                             # evicts "Dollar steady"
    await enricher.probabilities("Dollar steady")
    assert len(pipeline.calls) == 4


@pytest.mark.asyncio
async def test_enricher_returns_none_when_inference_fails() -> None:
    enricher = FinBERTEnricher()

    def broken(text, **kwargs):
        raise RuntimeError("sequence too long")

    enricher._pipeline = broken
    assert await enricher.probabilities("x") is None
    assert await enricher.score("x") == 0.0

