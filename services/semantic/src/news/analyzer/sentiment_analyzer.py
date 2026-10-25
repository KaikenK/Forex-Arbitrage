from __future__ import annotations

from collections import deque
from datetime import UTC
from datetime import datetime
from statistics import fmean
from statistics import pstdev

import structlog

from ..interfaces import NewsAnalyzer
from ..metrics import sentiment_signals_total
from ..models import SentimentSignal
from ..models import UnifiedNewsEvent
from .config import AnalyzerConfig
from .context_tagger import ContextTagger

DEFAULT_SOURCE_WEIGHT = 0.5
MIN_RECENCY_FACTOR = 0.5
MAX_ABSOLUTE_SCORE = 1.0


def utc_now() -> datetime:
    return datetime.now(UTC)


class SentimentAnalyzer(NewsAnalyzer):
    def __init__(self, config: AnalyzerConfig, tagger: ContextTagger) -> None:
        """Initializes the sentiment analyzer."""
        self._config = config
        self._tagger = tagger
        self._logger = structlog.get_logger().bind(component=self.__class__.__name__)
        self._symbol_history: dict[str, deque[float]] = {}

    async def analyze(self, event: UnifiedNewsEvent) -> list[SentimentSignal]:
        """Analyzes a unified event into sentiment signals."""
        if not event.symbols:
            self._logger.warning("sentiment_analyzer_no_symbols", event_id=event.event_id)
            return []

        source_weight = self._source_weight(event)
        recency_factor = self._recency_factor(event)
        context_tag = self._tagger.tag(event.headline)
        produced_at = utc_now()
        signals: list[SentimentSignal] = []

        for symbol in event.symbols:
            z_score = self._z_score(symbol, event.raw_sentiment)
            normalized_score = self._clamp(z_score)
            confidence = source_weight * recency_factor * min(MAX_ABSOLUTE_SCORE, abs(z_score))
            signals.append(
                SentimentSignal(
                    event_id=event.event_id,
                    symbol=symbol,
                    score=normalized_score,
                    confidence=confidence,
                    context_tag=context_tag,
                    produced_at=produced_at,
                ),
            )
            sentiment_signals_total.labels(
                symbol=symbol,
                context_tag=context_tag.value.casefold(),
            ).inc()

        return signals

    def _source_weight(self, event: UnifiedNewsEvent) -> float:
        raw_source = getattr(event.source, "value", event.source)
        source_key = str(raw_source).casefold()
        return self._config.source_weights.get(source_key, DEFAULT_SOURCE_WEIGHT)

    def _recency_factor(self, event: UnifiedNewsEvent) -> float:
        age_seconds = max((utc_now() - event.published_at).total_seconds(), 0.0)
        decay_progress = min(age_seconds / self._config.recency_window_seconds, 1.0)
        return 1.0 - ((1.0 - MIN_RECENCY_FACTOR) * decay_progress)

    def _z_score(self, symbol: str, raw_sentiment: float) -> float:
        history = self._symbol_history.setdefault(symbol, deque(maxlen=self._config.zscore_window))
        if len(history) < 2:
            z_score = raw_sentiment
        else:
            baseline = fmean(history)
            deviation = pstdev(history)
            if deviation == 0:
                z_score = raw_sentiment
            else:
                z_score = (raw_sentiment - baseline) / deviation

        history.append(raw_sentiment)
        return z_score

    def _clamp(self, value: float) -> float:
        return max(-MAX_ABSOLUTE_SCORE, min(MAX_ABSOLUTE_SCORE, value))
