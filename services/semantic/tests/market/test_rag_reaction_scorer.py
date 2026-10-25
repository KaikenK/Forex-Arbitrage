from __future__ import annotations

import json
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from market.rag_reaction_scorer import RagReactionScorer
from market.models import MarketReactionSignal
from market.schemas import AnalysisContext
from market.schemas import ArticleInput
from market.interfaces import EventReactionScorer
from news.models import SentimentSource
from news.models import SourceType
from news.models import UnifiedNewsEvent
from retrieval.embedder import LocalHashingEmbedder
from retrieval.index import LocalEvidenceIndex
from retrieval.schemas import RetrievalDocument


class StubReactionScorer(EventReactionScorer):
    def __init__(self, signals: list[MarketReactionSignal]) -> None:
        self._signals = signals

    async def score(self, context: AnalysisContext) -> list[MarketReactionSignal]:
        return list(self._signals)


def _make_context(headline: str, pair: str = "USD/INR") -> AnalysisContext:
    event = UnifiedNewsEvent(
        event_id="test-event",
        schema_version=1,
        source=SourceType.RSS,
        headline=headline,
        body=None,
        published_at=datetime(2025, 6, 1, 10, 0, tzinfo=UTC),
        symbols=[pair],
        raw_sentiment=0.0,
        sentiment_source=SentimentSource.NONE,
        ingested_at=datetime(2025, 6, 1, 10, 0, tzinfo=UTC),
    )
    article = ArticleInput(article_text=headline, headline=headline, currency_pair=pair)
    return AnalysisContext(article=article, event=event)


def _make_signal(pair: str = "USD/INR", direction: float = 1.0, confidence: float = 0.5) -> MarketReactionSignal:
    return MarketReactionSignal(
        event_id="test-event",
        symbol=pair,
        direction=direction,
        magnitude=0.3,
        confidence=confidence,
        horizon_minutes=60,
        key_drivers=["test"],
        produced_at=datetime(2025, 6, 1, 10, 0, tzinfo=UTC),
    )


def _build_rag_scorer(signals: list[MarketReactionSignal], documents: list[RetrievalDocument]) -> RagReactionScorer:
    embedder = LocalHashingEmbedder(dimensions=64)
    index = LocalEvidenceIndex(embedder)
    if documents:
        index.add_documents(documents)
    stub = StubReactionScorer(signals)
    return RagReactionScorer(base_scorer=stub, evidence_index=index)


def _make_doc(title: str, text: str, pair: str = "USD/INR", reaction_label: float | None = None) -> RetrievalDocument:
    metadata = {"pair": pair}
    if reaction_label is not None:
        metadata["reaction_label"] = str(reaction_label)
    return RetrievalDocument(
        document_id=f"doc-{title[:10]}",
        source="test",
        title=title,
        text=text,
        timestamp=datetime(2025, 5, 1, tzinfo=UTC),
        metadata=metadata,
    )


@pytest.mark.asyncio
async def test_rag_scorer_returns_base_signals_when_no_evidence() -> None:
    signal = _make_signal(confidence=0.5)
    scorer = _build_rag_scorer([signal], [])
    context = _make_context("RBI cuts rate")
    results = await scorer.score(context)
    assert len(results) == 1
    assert results[0].confidence == 0.5


@pytest.mark.asyncio
async def test_rag_scorer_boosts_confidence_with_relevant_evidence() -> None:
    signal = _make_signal(confidence=0.5)
    docs = [_make_doc("RBI rate cut", "RBI cuts repo rate rupee weakens")]
    scorer = _build_rag_scorer([signal], docs)
    context = _make_context("RBI cuts rate rupee weakens")
    results = await scorer.score(context)
    assert len(results) == 1
    assert results[0].confidence >= 0.5
    assert any("rag_support" in d for d in results[0].key_drivers)


@pytest.mark.asyncio
async def test_rag_scorer_passes_through_empty_signals() -> None:
    scorer = _build_rag_scorer([], [_make_doc("test", "test")])
    context = _make_context("any headline")
    results = await scorer.score(context)
    assert results == []


@pytest.mark.asyncio
async def test_rag_scorer_preserves_signal_fields() -> None:
    signal = _make_signal(pair="USD/INR", direction=-1.0, confidence=0.6)
    docs = [_make_doc("rupee falls", "dollar strengthens against rupee")]
    scorer = _build_rag_scorer([signal], docs)
    context = _make_context("dollar rises against rupee")
    results = await scorer.score(context)
    assert results[0].symbol == "USD/INR"
    assert results[0].direction == -1.0
    assert results[0].magnitude == 0.3
    assert results[0].horizon_minutes == 60


@pytest.mark.asyncio
async def test_rag_scorer_penalizes_contradictory_evidence() -> None:
    signal = _make_signal(direction=1.0, confidence=0.7)
    docs = [_make_doc("contradictory analogue", "historical analogue moved lower", reaction_label=-0.02)]
    scorer = _build_rag_scorer([signal], docs)
    context = _make_context("RBI event suggests upside")

    results = await scorer.score(context)

    assert results[0].confidence < 0.7
    assert any("rag_contradictory" in driver for driver in results[0].key_drivers)
