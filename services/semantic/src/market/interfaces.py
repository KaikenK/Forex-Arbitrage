from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from news.models import UnifiedNewsEvent
from retrieval.models import RetrievedEvidence

from .history_filters import HistoryFilters
from .models import ArticleAnalysis
from .models import MarketReactionSignal
from .schemas import AnalysisContext
from .schemas import ArticleInput
from .schemas import SentimentInference
from .schemas import ServiceHealth
from .schemas import ServiceMetrics


class MarketReactionAnalyzer(ABC):
    @abstractmethod
    async def analyze(self, context: AnalysisContext) -> ArticleAnalysis:
        """Produces a forex market analysis result for one article."""


class EventSentimentScorer(ABC):
    @abstractmethod
    async def score(self, context: AnalysisContext) -> SentimentInference:
        """Scores article sentiment for a prepared analysis context."""


class EventReactionScorer(ABC):
    @abstractmethod
    async def score(self, context: AnalysisContext) -> list[MarketReactionSignal]:
        """Predicts market-reaction signals for a prepared analysis context."""


class MarketAnalysisService(ABC):
    @abstractmethod
    async def analyze_article(self, article: ArticleInput) -> ArticleAnalysis:
        """Analyzes one raw article input into a market result."""

    @abstractmethod
    async def list_history(self, limit: int = 20, *, filters: HistoryFilters | None = None) -> list[ArticleAnalysis]:
        """Returns recent article analyses from service history."""

    @abstractmethod
    async def get_health(self) -> ServiceHealth:
        """Returns the current health status of the analysis service."""

    @abstractmethod
    async def get_metrics(self) -> ServiceMetrics:
        """Returns service metrics for model and pipeline inspection."""
