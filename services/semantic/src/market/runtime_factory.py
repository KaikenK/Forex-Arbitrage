from __future__ import annotations

from pathlib import Path

import structlog

from retrieval.cache_paths import embedding_cache_dir
from retrieval.embedder import LocalHashingEmbedder
from retrieval.index import LocalEvidenceIndex
from retrieval.text_embedder import TextEmbedder

from .history_store import JsonlAnalysisHistoryStore
from .analyzer import UnifiedArticleAnalyzer
from .artifact_reaction_scorer import ArtifactReactionScorer
from .empty_evidence_retriever import EmptyEvidenceRetriever
from .historical_evidence_retriever import HistoricalTableEvidenceRetriever
from .keyword_sentiment_scorer import KeywordSentimentScorer
from .rag_reaction_scorer import RagReactionScorer
from .service import UnifiedArticleAnalysisService

_logger = structlog.get_logger().bind(component="runtime_factory")


def _build_sentiment_scorer(use_finbert: bool):
    """Builds a sentiment scorer — FinBERT if requested, else keyword baseline."""
    if not use_finbert:
        return KeywordSentimentScorer()
    try:
        from .finbert_sentiment_scorer import FinBERTSentimentScorer
        return FinBERTSentimentScorer()
    except ImportError:
        _logger.warning("finbert_import_failed_using_keyword_fallback")
        return KeywordSentimentScorer()


class SentimentTap:
    """Passes the analyzer's sentiment through unchanged and reports it to `on_score`.

    Lets an analysis observer use the score the analyzer already computed (no second
    FinBERT pass). Only installed when an observer asks for it.
    """

    def __init__(self, inner, on_score) -> None:
        self._inner = inner
        self._on_score = on_score

    async def score(self, context):
        inference = await self._inner.score(context)
        try:
            self._on_score(context.event.event_id, inference)
        except Exception:
            _logger.warning("sentiment_tap_failed", exc_info=True)
        return inference


def build_runtime_service(
    reaction_artifact_path: Path,
    training_table_path: Path | None = None,
    preferred_horizon_minutes: int | None = None,
    embedder: TextEmbedder | None = None,
    use_rag: bool = False,
    use_finbert: bool = False,
    history_store: JsonlAnalysisHistoryStore | None = None,
    analysis_observer=None,
    sentiment_tap=None,
) -> UnifiedArticleAnalysisService:
    """Builds a local runtime analysis service from trained artifacts.

    ``analysis_observer(article, analysis)`` runs after each analysis; ``sentiment_tap
    (event_id, inference)`` receives the analyzer's sentiment. Both default to off, which
    leaves the service exactly as before.
    """
    sentiment_scorer = _build_sentiment_scorer(use_finbert)
    if sentiment_tap is not None:
        sentiment_scorer = SentimentTap(sentiment_scorer, sentiment_tap)
    reaction_scorer = ArtifactReactionScorer.from_path(
        artifact_path=reaction_artifact_path,
        preferred_horizon_minutes=preferred_horizon_minutes,
        embedder=embedder,
    )

    resolved_embedder = embedder or LocalHashingEmbedder(dimensions=reaction_scorer.embedding_dimensions)

    # Load and index documents once — shared between evidence retriever and RAG scorer.
    shared_index: LocalEvidenceIndex | None = None
    shared_documents: list | None = None
    if training_table_path is not None:
        from .historical_evidence_retriever import _load_documents
        shared_documents = _load_documents(training_table_path)
        # Persist corpus embeddings across restarts; without this, startup re-embeds the
        # whole evidence table, which grows from seconds to minutes as the dataset grows.
        shared_index = LocalEvidenceIndex(resolved_embedder, embedding_cache_dir=embedding_cache_dir())
        shared_index.add_documents(shared_documents)

    if shared_index is not None and shared_documents is not None:
        evidence_retriever = HistoricalTableEvidenceRetriever(
            index=shared_index,
            documents_by_id={doc.document_id: doc for doc in shared_documents},
        )
    else:
        evidence_retriever = EmptyEvidenceRetriever()

    final_scorer = reaction_scorer
    if use_rag and shared_index is not None:
        final_scorer = RagReactionScorer(
            base_scorer=reaction_scorer,
            evidence_index=shared_index,
        )

    analyzer = UnifiedArticleAnalyzer(
        sentiment_scorer=sentiment_scorer,
        reaction_scorer=final_scorer,
        evidence_retriever=evidence_retriever,
    )
    return UnifiedArticleAnalysisService(analyzer=analyzer, history_store=history_store,
                                         analysis_observer=analysis_observer)
