from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from enum import Enum

from news.models import SentimentSource
from news.models import SourceType
from news.models import UnifiedNewsEvent

from .models import KeyDriver


class ServiceHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True)
class ArticleInput:
    article_text: str
    headline: str | None = None
    source: SourceType | None = None
    published_at: datetime | None = None
    external_id: str | None = None
    event_category: str | None = None
    macro_event_type: str | None = None
    currency_pair: str | None = None
    scheduled: bool | None = None
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    standardized_surprise: float | None = None
    calendar_source: str | None = None
    source_url: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    # Extra pairs to score in the same pass. FinBERT runs once per article; each extra
    # pair only adds a linear reaction score (microseconds). `currency_pair`, when set,
    # stays the primary pair that article-level fields describe.
    currency_pairs: list[str] | None = None


@dataclass(frozen=True)
class AnalysisContext:
    article: ArticleInput
    event: UnifiedNewsEvent


@dataclass(frozen=True)
class SentimentInference:
    score: float
    confidence: float
    source: SentimentSource
    key_drivers: list[KeyDriver] = field(default_factory=list)


@dataclass(frozen=True)
class ServiceHealth:
    status: ServiceHealthStatus
    history_size: int
    last_analysis_at: datetime | None


@dataclass(frozen=True)
class ServiceMetrics:
    analysis_requests: int
    analyses_completed: int
    average_confidence: float
    history_size: int
    last_analysis_at: datetime | None
