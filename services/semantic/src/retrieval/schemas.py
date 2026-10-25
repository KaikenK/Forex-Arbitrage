from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RetrievalDocument:
    document_id: str
    source: str
    title: str
    text: str
    timestamp: datetime
    metadata: dict[str, str]


@dataclass(frozen=True)
class RetrievalResult:
    document_id: str
    source: str
    title: str
    timestamp: datetime
    similarity: float
    relevance_reason: str
    metadata: dict[str, str]
