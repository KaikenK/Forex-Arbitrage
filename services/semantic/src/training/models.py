from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from news.models import SourceType


class DatasetSplit(str, Enum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


@dataclass(frozen=True)
class PriceBar:
    symbol: str
    timestamp: datetime
    close: float


@dataclass(frozen=True)
class TrainingExample:
    example_id: str
    source: SourceType
    headline: str
    body: str | None
    published_at: datetime
    affected_pairs: list[str]
    sentiment_label: float
    market_reaction_label: float
    confidence_label: float
    evidence_ids: list[str]


@dataclass(frozen=True)
class MarketReactionTrainingRow:
    article_text: str
    timestamp: datetime
    pair: str
    horizon_minutes: int
    sentiment_label: float
    reaction_label: float
    magnitude_label: float
    source_metadata: dict[str, Any]
    event_category: str | None = None
    macro_event_type: str | None = None
    scheduled: bool = False
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    standardized_surprise: float | None = None
    calendar_source: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class TimeSplitTrainingRow:
    row: MarketReactionTrainingRow
    split: DatasetSplit
