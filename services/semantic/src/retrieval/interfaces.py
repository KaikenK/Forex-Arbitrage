from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from market.models import MarketReactionSignal
from market.schemas import AnalysisContext

from .models import RetrievedEvidence


class EvidenceRetriever(ABC):
    @abstractmethod
    async def retrieve(
        self,
        context: AnalysisContext,
        signals: list[MarketReactionSignal],
    ) -> list[RetrievedEvidence]:
        """Retrieves supporting evidence for a normalized article."""
