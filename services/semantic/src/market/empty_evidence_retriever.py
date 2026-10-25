from __future__ import annotations

from retrieval.interfaces import EvidenceRetriever
from retrieval.models import RetrievedEvidence

from .models import MarketReactionSignal
from .schemas import AnalysisContext


class EmptyEvidenceRetriever(EvidenceRetriever):
    async def retrieve(
        self,
        context: AnalysisContext,
        signals: list[MarketReactionSignal],
    ) -> list[RetrievedEvidence]:
        """Returns no evidence when no historical table is configured."""
        del context, signals
        return []
