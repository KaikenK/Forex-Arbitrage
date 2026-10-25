"""Rolling USD/INR live sentiment aggregator.

Maintains a sliding window of recent news sentiment scores and produces
an aggregated snapshot (overall sentiment, confidence, counts, top drivers)
refreshed on demand.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class SentimentItem:
    """One scored news item in the sentiment window."""
    headline: str
    sentiment_score: float
    confidence: float
    pair: str
    category: str
    source: str
    timestamp: datetime


@dataclass(frozen=True)
class LiveSentimentSnapshot:
    """Aggregated sentiment for a pair over a time window."""
    pair: str
    overall_sentiment: float
    confidence: float
    bullish_count: int
    bearish_count: int
    neutral_count: int
    item_count: int
    top_drivers: list[dict[str, str]]
    updated_at: datetime
    window_minutes: int


class LiveSentimentAggregator:
    """Thread-safe sliding-window sentiment aggregator."""

    def __init__(self, max_items: int = 500) -> None:
        """Initializes the aggregator with a bounded item buffer."""
        self._items: deque[SentimentItem] = deque(maxlen=max_items)
        self._lock = asyncio.Lock()

    async def record(
        self,
        headline: str,
        sentiment_score: float,
        confidence: float,
        pair: str = "USD/INR",
        category: str = "general",
        source: str = "unknown",
        timestamp: datetime | None = None,
    ) -> None:
        """Record a new sentiment item into the window."""
        item = SentimentItem(
            headline=headline,
            sentiment_score=max(-1.0, min(1.0, sentiment_score)),
            confidence=max(0.0, min(1.0, confidence)),
            pair=pair.upper(),
            category=category,
            source=source,
            timestamp=timestamp or datetime.now(UTC),
        )
        async with self._lock:
            self._items.append(item)

    async def aggregate(
        self,
        pair: str = "USD/INR",
        window_minutes: int = 5,
    ) -> LiveSentimentSnapshot:
        """Compute aggregated sentiment snapshot for the given pair and window."""
        now = datetime.now(UTC)
        cutoff = now - timedelta(minutes=window_minutes)

        async with self._lock:
            relevant = [
                item for item in self._items
                if item.pair == pair.upper() and item.timestamp >= cutoff
            ]

        if not relevant:
            return LiveSentimentSnapshot(
                pair=pair,
                overall_sentiment=0.0,
                confidence=0.0,
                bullish_count=0,
                bearish_count=0,
                neutral_count=0,
                item_count=0,
                top_drivers=[],
                updated_at=now,
                window_minutes=window_minutes,
            )

        # Recency-weighted average: more recent = higher weight
        total_weight = 0.0
        weighted_sentiment = 0.0
        weighted_confidence = 0.0
        bullish = bearish = neutral = 0

        for item in relevant:
            age_minutes = max(0.0, (now - item.timestamp).total_seconds() / 60.0)
            recency_weight = max(0.1, 1.0 - age_minutes / max(1.0, window_minutes))
            weight = recency_weight * item.confidence

            weighted_sentiment += item.sentiment_score * weight
            weighted_confidence += item.confidence * weight
            total_weight += weight

            if item.sentiment_score > 0.1:
                bullish += 1
            elif item.sentiment_score < -0.1:
                bearish += 1
            else:
                neutral += 1

        overall = weighted_sentiment / total_weight if total_weight > 0 else 0.0
        avg_conf = weighted_confidence / total_weight if total_weight > 0 else 0.0

        # Top drivers: most impactful recent headlines sorted by abs sentiment
        sorted_items = sorted(relevant, key=lambda i: abs(i.sentiment_score), reverse=True)
        top_drivers = [
            {
                "headline": item.headline,
                "sentiment": f"{item.sentiment_score:+.2f}",
                "source": item.source,
                "category": item.category,
            }
            for item in sorted_items[:5]
        ]

        return LiveSentimentSnapshot(
            pair=pair,
            overall_sentiment=round(max(-1.0, min(1.0, overall)), 4),
            confidence=round(max(0.0, min(1.0, avg_conf)), 4),
            bullish_count=bullish,
            bearish_count=bearish,
            neutral_count=neutral,
            item_count=len(relevant),
            top_drivers=top_drivers,
            updated_at=now,
            window_minutes=window_minutes,
        )

    async def item_count(self) -> int:
        """Total items in buffer (across all pairs)."""
        async with self._lock:
            return len(self._items)
