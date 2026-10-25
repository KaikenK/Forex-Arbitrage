from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import cast

from .models import SentimentSignal
from .models import UnifiedNewsEvent


class NewsIngester(ABC):
    @abstractmethod
    async def ingest(self) -> AsyncIterator[dict]:
        """Yields raw article or event payloads."""
        if False:
            yield cast(dict, {})
        raise NotImplementedError

    @abstractmethod
    def is_healthy(self) -> bool:
        """Returns whether the ingester is currently healthy."""


class NewsNormalizer(ABC):
    @abstractmethod
    def normalize(self, raw: dict) -> UnifiedNewsEvent:
        """Converts a raw article payload into a unified event."""


class NewsQueue(ABC):
    @abstractmethod
    async def publish(self, event: UnifiedNewsEvent) -> None:
        """Publishes a normalized article event to the queue."""

    @abstractmethod
    async def subscribe(self) -> AsyncIterator[UnifiedNewsEvent]:
        """Yields normalized article events from the queue."""
        if False:
            yield cast(UnifiedNewsEvent, None)
        raise NotImplementedError


class SentimentEnricher(ABC):
    @abstractmethod
    async def enrich(self, event: UnifiedNewsEvent) -> UnifiedNewsEvent:
        """Enriches a normalized article event with foundation signals."""


class NewsAnalyzer(ABC):
    @abstractmethod
    async def analyze(self, event: UnifiedNewsEvent) -> list[SentimentSignal]:
        """Analyzes a normalized article event into legacy sentiment signals."""
