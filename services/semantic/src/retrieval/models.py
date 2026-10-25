from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class EvidenceType(str, Enum):
    ARTICLE = "ARTICLE"
    PRICE_ACTION = "PRICE_ACTION"
    ECONOMIC_CALENDAR = "ECONOMIC_CALENDAR"
    CENTRAL_BANK = "CENTRAL_BANK"
    MARKET_DATA = "MARKET_DATA"
    OTHER = "OTHER"


@dataclass(frozen=True)
class RetrievedEvidence:
    evidence_id: str
    source: str
    evidence_type: EvidenceType
    title: str
    snippet: str
    url: str | None
    published_at: datetime | None
    relevance_score: float
    pair: str | None = None
    horizon_minutes: int | None = None
    event_category: str | None = None
    macro_event_type: str | None = None
    scheduled: bool | None = None
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    standardized_surprise: float | None = None
    calendar_source: str | None = None
    matching_reason: str | None = None
    stance: str | None = None
    historical_reaction: float | None = None
    historical_direction: float | None = None
