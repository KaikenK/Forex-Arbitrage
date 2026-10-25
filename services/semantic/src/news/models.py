from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SourceType(str, Enum):
    FINNHUB = "FINNHUB"
    RSS = "RSS"
    OFFICIAL = "OFFICIAL"
    REDDIT = "REDDIT"
    ALPHA_VANTAGE = "ALPHA_VANTAGE"


class SentimentSource(str, Enum):
    ALPHA_VANTAGE = "ALPHA_VANTAGE"
    FINBERT = "FINBERT"
    NONE = "NONE"


class ContextTag(str, Enum):
    MACRO = "MACRO"
    CENTRAL_BANK = "CENTRAL_BANK"
    GEOPOLITICAL = "GEOPOLITICAL"
    EARNINGS = "EARNINGS"
    COMMODITY = "COMMODITY"
    FISCAL = "FISCAL"
    POLICY = "POLICY"
    RISK_SENTIMENT = "RISK_SENTIMENT"
    OTHER = "OTHER"


@dataclass(frozen=True)
class UnifiedNewsEvent:
    event_id: str
    schema_version: int
    source: SourceType
    headline: str
    body: str | None
    published_at: datetime
    symbols: list[str]
    raw_sentiment: float
    sentiment_source: SentimentSource
    ingested_at: datetime


@dataclass(frozen=True)
class SentimentSignal:
    event_id: str
    symbol: str
    score: float
    confidence: float
    context_tag: ContextTag
    produced_at: datetime


NormalizedArticleEvent = UnifiedNewsEvent
