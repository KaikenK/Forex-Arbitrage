from __future__ import annotations

import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from market.analyzer import UnifiedArticleAnalyzer
from market.models import KeyDriver
from market.models import MarketReactionSignal
from market.models import RiskFlag
from market.schemas import AnalysisContext
from market.schemas import ArticleInput
from market.schemas import SentimentInference
from market.service import UnifiedArticleAnalysisService
from news.models import SentimentSource
from retrieval.interfaces import EvidenceRetriever
from retrieval.models import EvidenceType
from retrieval.models import RetrievedEvidence


class FakeSentimentScorer:
    async def score(self, context: AnalysisContext) -> SentimentInference:
        """Returns a deterministic sentiment inference for testing."""
        assert "EUR/USD" in context.event.symbols
        return SentimentInference(
            score=0.7,
            confidence=0.8,
            source=SentimentSource.FINBERT,
            key_drivers=[
                KeyDriver(
                    label="ECB guidance",
                    rationale="The article describes a hawkish ECB stance.",
                    evidence_ids=["ev-1"],
                ),
            ],
        )


class FakeReactionScorer:
    async def score(self, context: AnalysisContext) -> list[MarketReactionSignal]:
        """Returns one deterministic market-reaction signal for testing."""
        return [
            MarketReactionSignal(
                event_id=context.event.event_id,
                symbol=context.event.symbols[0],
                direction=1.0,
                magnitude=0.6,
                confidence=0.9,
                horizon_minutes=15,
                key_drivers=["rate-path divergence"],
                produced_at=context.event.ingested_at,
            ),
        ]


class FakeEvidenceRetriever(EvidenceRetriever):
    async def retrieve(
        self,
        context: AnalysisContext,
        signals: list[MarketReactionSignal],
    ) -> list[RetrievedEvidence]:
        """Returns deterministic evidence rows for testing."""
        del context, signals
        return [
            RetrievedEvidence(
                evidence_id="ev-1",
                source="historical-archive",
                evidence_type=EvidenceType.ECONOMIC_CALENDAR,
                title="ECB rate surprise lifted EUR/USD",
                snippet="A prior ECB surprise pushed EUR/USD higher intraday.",
                url=None,
                published_at=datetime(2026, 3, 24, 12, 0, tzinfo=UTC),
                relevance_score=0.92,
            ),
            RetrievedEvidence(
                evidence_id="ev-2",
                source="historical-archive",
                evidence_type=EvidenceType.ARTICLE,
                title="Euro rally followed hawkish guidance",
                snippet="Markets repriced the euro after hawkish central bank language.",
                url=None,
                published_at=datetime(2026, 3, 20, 9, 0, tzinfo=UTC),
                relevance_score=0.81,
            ),
        ]


@pytest.mark.asyncio
async def test_unified_article_analysis_service_scores_article_without_watchlist_gate() -> None:
    analyzer = UnifiedArticleAnalyzer(
        sentiment_scorer=FakeSentimentScorer(),
        reaction_scorer=FakeReactionScorer(),
        evidence_retriever=FakeEvidenceRetriever(),
        clock=lambda: datetime(2026, 3, 25, 12, 0, tzinfo=UTC),
    )
    service = UnifiedArticleAnalysisService(
        analyzer=analyzer,
        clock=lambda: datetime(2026, 3, 25, 12, 0, tzinfo=UTC),
    )

    analysis = await service.analyze_article(
        ArticleInput(
            article_text=(
                "ECB signals hawkish path for the euro\n"
                "The euro strengthened against the dollar after policymakers signaled fewer cuts."
            ),
            event_category="central-bank",
        ),
    )

    assert analysis.sentiment_score == pytest.approx(0.7)
    assert analysis.market_score == pytest.approx(0.6)
    assert analysis.affected_symbols == ["EUR/USD"]
    # 0.30*0.8 sentiment + 0.50*0.9 market + 0.20*0.48 evidence.
    # The evidence term is 0.48, not the old ~0.67: both rows are highly relevant
    # but carry no stance, and only 2 of the expected 5 were retrieved. Thin,
    # non-committal evidence should support less confidence than a full,
    # agreeing retrieval -- the old count-based term could not express that.
    assert analysis.confidence == pytest.approx(0.786, abs=0.01)
    assert [driver.label for driver in analysis.key_drivers] == ["ECB guidance", "rate-path divergence"]
    assert [evidence.evidence_id for evidence in analysis.evidence] == ["ev-1", "ev-2"]
    assert RiskFlag.HIGH_EVENT_RISK in analysis.risk_flags


@pytest.mark.asyncio
async def test_unified_article_analysis_service_tracks_history_and_metrics() -> None:
    analyzer = UnifiedArticleAnalyzer(
        sentiment_scorer=FakeSentimentScorer(),
        reaction_scorer=FakeReactionScorer(),
        evidence_retriever=FakeEvidenceRetriever(),
        clock=lambda: datetime(2026, 3, 25, 15, 0, tzinfo=UTC),
    )
    service = UnifiedArticleAnalysisService(
        analyzer=analyzer,
        clock=lambda: datetime(2026, 3, 25, 15, 0, tzinfo=UTC),
    )

    await service.analyze_article(
        ArticleInput(
            article_text="Euro rallies versus dollar after ECB language turns hawkish.",
            headline="ECB shifts tone",
            currency_pair="EUR/USD",
        ),
    )

    history = await service.list_history()
    metrics = await service.get_metrics()

    assert len(history) == 1
    assert metrics.analysis_requests == 1
    assert metrics.analyses_completed == 1
    assert metrics.average_confidence == pytest.approx(0.786, abs=0.01)
