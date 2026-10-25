from __future__ import annotations

from dataclasses import replace
from datetime import UTC
from datetime import datetime

import structlog

from ..interfaces import SentimentEnricher as SentimentEnricherInterface
from ..models import SentimentSource
from ..models import UnifiedNewsEvent
from .alpha_vantage_enricher import AlphaVantageEnricher
from .finbert_enricher import FinBERTEnricher

DEFAULT_MAX_AGE_SECONDS = 300


def utc_now() -> datetime:
    return datetime.now(UTC)


class SentimentEnricher(SentimentEnricherInterface):
    def __init__(
        self,
        av_enricher: AlphaVantageEnricher,
        finbert: FinBERTEnricher,
        priority_symbols: list[str],
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    ) -> None:
        """Initializes the sentiment enricher orchestration."""
        self._av_enricher = av_enricher
        self._finbert = finbert
        self._priority_symbols = priority_symbols
        self._max_age_seconds = max_age_seconds
        self._logger = structlog.get_logger().bind(component=self.__class__.__name__)

    async def enrich(self, event: UnifiedNewsEvent) -> UnifiedNewsEvent:
        """Enriches a unified event with Alpha Vantage or FinBERT sentiment."""
        if not event.symbols:
            return event
        if self._is_stale(event):
            return event

        priority_symbol = self._priority_symbol(event.symbols)
        if priority_symbol is not None:
            av_score = await self._av_enricher.enrich_score(priority_symbol, event.headline)
            if av_score is not None:
                return replace(
                    event,
                    raw_sentiment=av_score,
                    sentiment_source=SentimentSource.ALPHA_VANTAGE,
                )

        finbert_score = await self._finbert.score(self._event_text(event))
        return replace(
            event,
            raw_sentiment=finbert_score,
            sentiment_source=SentimentSource.FINBERT,
        )

    def _priority_symbol(self, event_symbols: list[str]) -> str | None:
        for symbol in self._priority_symbols:
            if symbol in event_symbols:
                return symbol
        return None

    def _is_stale(self, event: UnifiedNewsEvent) -> bool:
        age_seconds = (utc_now() - event.published_at).total_seconds()
        return age_seconds > self._max_age_seconds

    def _event_text(self, event: UnifiedNewsEvent) -> str:
        body = event.body or ""
        return " ".join(part for part in (event.headline, body) if part)

    def get_budget_status(self) -> dict[str, int | str]:
        """Returns the underlying Alpha Vantage budget status."""
        return self._av_enricher.get_budget_status()
