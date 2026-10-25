from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from .models import AnalyzeArticleRequest
from .models import AnalyzeArticleResponse


class ArticleAnalysisService(ABC):
    @abstractmethod
    async def analyze(self, request: AnalyzeArticleRequest) -> AnalyzeArticleResponse:
        """Analyzes one article request and returns a structured response."""
