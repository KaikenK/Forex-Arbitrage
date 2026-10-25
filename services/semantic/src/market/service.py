from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
import hashlib
from pathlib import Path

import structlog

from news.models import SentimentSource
from news.models import SourceType
from news.models import UnifiedNewsEvent

from .history_filters import HistoryFilters
from .interfaces import MarketAnalysisService
from .history_store import JsonlAnalysisHistoryStore
from .interfaces import MarketReactionAnalyzer
from .models import ArticleAnalysis
from .schemas import AnalysisContext
from .schemas import ArticleInput
from .schemas import ServiceHealth
from .schemas import ServiceHealthStatus
from .schemas import ServiceMetrics

DEFAULT_HISTORY_LIMIT = 20
DEFAULT_MAX_HISTORY = 500


class UnifiedArticleAnalysisService(MarketAnalysisService):
    logger = structlog.get_logger().bind(component="UnifiedArticleAnalysisService")

    def __init__(
        self,
        analyzer: MarketReactionAnalyzer,
        clock: Callable[[], datetime] | None = None,
        default_source: SourceType = SourceType.RSS,
        max_history: int = DEFAULT_MAX_HISTORY,
        history_store: JsonlAnalysisHistoryStore | None = None,
        analysis_observer: Callable[[ArticleInput, ArticleAnalysis], None] | None = None,
    ) -> None:
        """Initializes the unified article analysis service.

        ``analysis_observer`` is called once per completed analysis (the currency-bias
        tracker uses it); a failing observer is logged and never fails the analysis.
        """
        self._analysis_observer = analysis_observer
        self._analyzer = analyzer
        self._clock = clock or _utc_now
        self._default_source = default_source
        self._max_history = max_history
        self._history_store = history_store
        self._history: list[ArticleAnalysis] = self._load_history(history_store)
        self._analysis_requests = 0
        self._analyses_completed = 0
        self._confidence_total = 0.0
        self._last_analysis_at: datetime | None = self._history[0].produced_at if self._history else None
        self._lock = asyncio.Lock()

    async def analyze_article(self, article: ArticleInput) -> ArticleAnalysis:
        """Analyzes one raw article input into a market result."""
        context = self._build_context(article)
        async with self._lock:
            self._analysis_requests += 1

        analysis = await self._analyzer.analyze(context)

        async with self._lock:
            self._history.insert(0, analysis)
            del self._history[self._max_history :]
            if self._history_store is not None:
                self._history_store.append(analysis)
            self._analyses_completed += 1
            self._confidence_total += analysis.confidence
            self._last_analysis_at = analysis.produced_at
        if self._analysis_observer is not None:
            try:
                self._analysis_observer(article, analysis)
            except Exception:
                self.logger.warning("analysis_observer_failed", exc_info=True)
        return analysis

    async def list_history(
        self,
        limit: int = DEFAULT_HISTORY_LIMIT,
        *,
        filters: HistoryFilters | None = None,
    ) -> list[ArticleAnalysis]:
        """Returns recent article analyses from service history."""
        if limit <= 0:
            raise ValueError("limit must be greater than zero.")
        async with self._lock:
            if filters is None:
                return list(self._history[:limit])
            return [analysis for analysis in self._history if filters.matches(analysis)][:limit]

    async def get_health(self) -> ServiceHealth:
        """Returns the current health status of the analysis service."""
        async with self._lock:
            return ServiceHealth(
                status=ServiceHealthStatus.HEALTHY,
                history_size=len(self._history),
                last_analysis_at=self._last_analysis_at,
            )

    async def get_metrics(self) -> ServiceMetrics:
        """Returns service metrics for model and pipeline inspection."""
        async with self._lock:
            average_confidence = 0.0
            if self._analyses_completed:
                average_confidence = self._confidence_total / self._analyses_completed
            return ServiceMetrics(
                analysis_requests=self._analysis_requests,
                analyses_completed=self._analyses_completed,
                average_confidence=average_confidence,
                history_size=len(self._history),
                last_analysis_at=self._last_analysis_at,
            )

    def _build_context(self, article: ArticleInput) -> AnalysisContext:
        normalized_text = article.article_text.strip()
        if not normalized_text:
            raise ValueError("article_text must be a non-empty string.")

        headline, body = _split_article(article.headline, normalized_text)
        now = self._clock()
        published_at = _normalize_published_at(article.published_at, now)
        source = article.source or self._default_source
        event_id = article.external_id or _stable_event_id(headline, body, published_at)
        # Primary pair first: article-level fields are computed from the first symbol.
        initial_symbols: list[str] = []
        for pair in [article.currency_pair, *(article.currency_pairs or [])]:
            if pair and pair.strip() and pair.strip().upper() not in initial_symbols:
                initial_symbols.append(pair.strip().upper())

        event = UnifiedNewsEvent(
            event_id=event_id,
            schema_version=1,
            source=source,
            headline=headline,
            body=body,
            published_at=published_at,
            symbols=initial_symbols,
            raw_sentiment=0.0,
            sentiment_source=SentimentSource.NONE,
            ingested_at=now,
        )
        return AnalysisContext(article=article, event=event)

    def _load_history(self, history_store: JsonlAnalysisHistoryStore | None) -> list[ArticleAnalysis]:
        if history_store is None:
            return []
        try:
            history = history_store.load()
        except OSError:
            self.logger.warning("analysis_history_store_load_failed", path=str(history_store.path), exc_info=True)
            return []
        return history[: self._max_history]


def _split_article(explicit_headline: str | None, article_text: str) -> tuple[str, str | None]:
    if explicit_headline is not None and explicit_headline.strip():
        return explicit_headline.strip(), article_text.strip() or None

    lines = [line.strip() for line in article_text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("article_text must contain non-whitespace content.")
    headline = lines[0]
    if len(lines) == 1:
        return headline, None
    body = "\n".join(lines[1:]).strip()
    return headline, body or None


def _normalize_published_at(published_at: datetime | None, now: datetime) -> datetime:
    if published_at is None:
        return now
    if published_at.tzinfo is None or published_at.utcoffset() is None:
        raise ValueError("published_at must be timezone-aware.")
    return published_at.astimezone(UTC)


def _stable_event_id(headline: str, body: str | None, published_at: datetime) -> str:
    digest_source = "|".join([headline, body or "", published_at.isoformat()])
    return hashlib.sha256(digest_source.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)
