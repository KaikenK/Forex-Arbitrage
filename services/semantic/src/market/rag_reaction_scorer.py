from __future__ import annotations

from collections.abc import Callable
from datetime import UTC
from datetime import datetime

import structlog

from retrieval.index import LocalEvidenceIndex
from retrieval.text_embedder import TextEmbedder

from .interfaces import EventReactionScorer
from .models import MarketReactionSignal
from .schemas import AnalysisContext

EVIDENCE_K = 5
RELEVANCE_BOOST_THRESHOLD = 0.35
MAX_CONFIDENCE_BOOST = 0.10
CONTRADICTION_PENALTY = 0.15


class RagReactionScorer(EventReactionScorer):
    """Wraps a base reaction scorer with RAG-based confidence adjustment."""

    logger = structlog.get_logger().bind(component="RagReactionScorer")

    def __init__(
        self,
        base_scorer: EventReactionScorer,
        evidence_index: LocalEvidenceIndex,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Initializes a RAG-augmented reaction scorer."""
        self._base_scorer = base_scorer
        self._evidence_index = evidence_index
        self._clock = clock or _utc_now

    @property
    def model_descriptor(self) -> str:
        """Describes the wrapped model, so the panel names the real predictor."""
        return getattr(self._base_scorer, "model_descriptor", "unknown base model")

    async def score(self, context: AnalysisContext) -> list[MarketReactionSignal]:
        """Scores using the base model, then adjusts using retrieved evidence."""
        base_signals = await self._base_scorer.score(context)
        if not base_signals:
            return base_signals

        query_text = _build_query(context)
        evidence_results = self._evidence_index.search(
            query_text=query_text,
            metadata_filters=None,
            limit=EVIDENCE_K,
        )

        if not evidence_results:
            return base_signals

        return [self._adjust_signal(signal, evidence_results, context) for signal in base_signals]

    def _adjust_signal(
        self,
        signal: MarketReactionSignal,
        evidence_results: list[tuple[object, float]],
        context: AnalysisContext,
    ) -> MarketReactionSignal:
        """Adjusts a signal's confidence based on evidence relevance."""
        pair_evidence = [
            (doc, sim)
            for doc, sim in evidence_results
            if getattr(doc, "metadata", {}).get("pair") == signal.symbol
        ]
        if not pair_evidence:
            pair_evidence = evidence_results

        avg_relevance = sum(sim for _, sim in pair_evidence) / len(pair_evidence)
        supportive_scores = [sim for doc, sim in pair_evidence if _document_stance(doc, signal.direction) == "supportive"]
        contradictory_scores = [sim for doc, sim in pair_evidence if _document_stance(doc, signal.direction) == "contradictory"]
        supportive_relevance = sum(supportive_scores) / len(supportive_scores) if supportive_scores else 0.0
        contradictory_relevance = sum(contradictory_scores) / len(contradictory_scores) if contradictory_scores else 0.0

        adjusted_confidence = signal.confidence
        if supportive_relevance >= RELEVANCE_BOOST_THRESHOLD:
            boost = min(supportive_relevance * MAX_CONFIDENCE_BOOST, MAX_CONFIDENCE_BOOST)
            adjusted_confidence = min(adjusted_confidence + boost, 1.0)
        elif avg_relevance < RELEVANCE_BOOST_THRESHOLD:
            adjusted_confidence = max(adjusted_confidence - CONTRADICTION_PENALTY * (1.0 - avg_relevance), 0.0)

        if contradictory_relevance > 0:
            adjusted_confidence = max(
                adjusted_confidence - min(contradictory_relevance * CONTRADICTION_PENALTY, CONTRADICTION_PENALTY),
                0.0,
            )

        enriched_drivers = list(signal.key_drivers)
        if supportive_scores:
            enriched_drivers.append(f"rag_supportive:{supportive_relevance:.2f}")
        if contradictory_scores:
            enriched_drivers.append(f"rag_contradictory:{contradictory_relevance:.2f}")
        if not supportive_scores and not contradictory_scores:
            if avg_relevance >= RELEVANCE_BOOST_THRESHOLD:
                enriched_drivers.append(f"rag_support:{avg_relevance:.2f}")
            else:
                enriched_drivers.append(f"rag_weak:{avg_relevance:.2f}")

        return MarketReactionSignal(
            event_id=signal.event_id,
            symbol=signal.symbol,
            direction=signal.direction,
            magnitude=signal.magnitude,
            confidence=adjusted_confidence,
            horizon_minutes=signal.horizon_minutes,
            key_drivers=enriched_drivers,
            produced_at=signal.produced_at,
        )


def _build_query(context: AnalysisContext) -> str:
    parts = [context.event.headline]
    if context.event.body:
        parts.append(context.event.body[:500])
    return " ".join(parts)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _document_stance(document: object, signal_direction: float) -> str:
    metadata = getattr(document, "metadata", {})
    reaction_value = metadata.get("reaction_label") if isinstance(metadata, dict) else None
    if reaction_value is None:
        return "neutral"
    try:
        historical_reaction = float(reaction_value)
    except (TypeError, ValueError):
        return "neutral"
    if signal_direction == 0 or historical_reaction == 0:
        return "neutral"
    if signal_direction * historical_reaction > 0:
        return "supportive"
    return "contradictory"
