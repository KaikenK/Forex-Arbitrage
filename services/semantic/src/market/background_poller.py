"""Background news poller for live sentiment aggregation.

Periodically fetches India-focused RSS feeds, scores each headline
via a sentiment scorer, and records results in the LiveSentimentAggregator.
Deduplicates by headline hash to avoid double-counting.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import UTC, datetime
from typing import Any

import structlog

from .interfaces import EventSentimentScorer
from .live_sentiment import LiveSentimentAggregator
from .schemas import AnalysisContext, ArticleInput

from news.models import SentimentSource, SourceType, UnifiedNewsEvent

_logger = structlog.get_logger().bind(component="BackgroundNewsPoller")

# India-focused + global forex RSS feeds for background polling
POLLER_RSS_URLS = (
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.livemint.com/rss/markets",
    "https://www.business-standard.com/rss/markets-106.rss",
    "https://www.moneycontrol.com/rss/markets.xml",
    "https://www.ndtv.com/rss/business/latest.xml",
    "https://www.forexlive.com/feed/news",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.fxstreet.com/rss/news",
)

# Keywords that indicate forex / India market relevance
_RELEVANCE_KEYWORDS = frozenset({
    "forex", "currency", "exchange rate", "dollar", "rupee", "inr",
    "usd", "rbi", "fed", "inflation", "cpi", "gdp", "pmi",
    "oil", "crude", "gold", "trade deficit", "fii", "fdi",
    "sensex", "nifty", "rate", "yield", "bond", "interest",
    "india", "monetary", "fiscal", "budget", "tariff", "sanction",
    "war", "conflict", "opec",
})

_PAIR_RE = re.compile(r"\b([A-Z]{3})[/\-]([A-Z]{3})\b")
# Only real currency/metal codes count as a pair. Without this guard the regex
# turns ordinary hyphenated words into fake pairs: "wait-and-see" -> AND/SEE,
# "mid-cap" -> MID/CAP, "has-not" -> HAS/NOT. routes/feed.py already guards its
# own copy of this regex; this path was missed and kept minting junk pairs.
_SUPPORTED_CURRENCIES = frozenset({
    "USD", "EUR", "JPY", "GBP", "CHF", "AUD", "NZD", "CAD",
    "INR", "CNY", "XAU", "XAG",
})
_CURRENCY_HINTS: dict[str, str] = {
    "dollar": "USD", "rupee": "INR", "euro": "EUR", "yen": "JPY",
    "pound": "GBP", "franc": "CHF", "india": "INR", "rbi": "INR",
    "fed ": "USD", "sensex": "INR", "nifty": "INR",
}


def _detect_pair(text: str) -> str:
    """Best-effort pair detection from headline text."""
    for base_currency, quote_currency in _PAIR_RE.findall(text.upper()):
        if base_currency in _SUPPORTED_CURRENCIES and quote_currency in _SUPPORTED_CURRENCIES:
            return f"{base_currency}/{quote_currency}"
    lowered = text.casefold()
    currencies: list[str] = []
    for hint, code in _CURRENCY_HINTS.items():
        if hint in lowered and code not in currencies:
            currencies.append(code)
    if len(currencies) >= 2:
        return f"{currencies[0]}/{currencies[1]}"
    if len(currencies) == 1:
        return f"USD/{currencies[0]}" if currencies[0] != "USD" else "USD/INR"
    return "USD/INR"


def _is_relevant(text: str) -> bool:
    lowered = text.casefold()
    return any(kw in lowered for kw in _RELEVANCE_KEYWORDS)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().casefold().encode()).hexdigest()[:16]


class BackgroundNewsPoller:
    """Polls RSS feeds periodically and feeds scored items into the aggregator."""

    def __init__(
        self,
        aggregator: LiveSentimentAggregator,
        sentiment_scorer: EventSentimentScorer,
        poll_interval_seconds: int = 300,
        rss_urls: tuple[str, ...] = POLLER_RSS_URLS,
    ) -> None:
        """Initializes the poller with an aggregator and scorer."""
        self._aggregator = aggregator
        self._scorer = sentiment_scorer
        self._interval = poll_interval_seconds
        self._rss_urls = rss_urls
        self._seen: set[str] = set()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._poll_loop())
        _logger.info("background_poller_started", interval=self._interval)

    async def stop(self) -> None:
        """Stop the background polling loop."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            _logger.info("background_poller_stopped")

    async def _poll_loop(self) -> None:
        """Infinite polling loop — fetch, score, record."""
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _logger.warning("poll_error", error=str(exc))
            await asyncio.sleep(self._interval)

    async def _poll_once(self) -> None:
        """Single poll iteration: fetch all RSS feeds and score new items."""
        entries = await self._fetch_rss()
        new_count = 0
        for entry in entries:
            title = entry.get("title", "").strip()
            if not title:
                continue
            text_key = _text_hash(title)
            if text_key in self._seen:
                continue
            self._seen.add(text_key)

            full_text = title + " " + entry.get("summary", "")
            if not _is_relevant(full_text):
                continue

            pair = _detect_pair(full_text)
            try:
                sentiment = await self._score_headline(title)
            except Exception as exc:
                _logger.debug("score_error", headline=title[:50], error=str(exc))
                continue

            await self._aggregator.record(
                headline=title,
                sentiment_score=sentiment.score,
                confidence=sentiment.confidence,
                pair=pair,
                category="rss_poll",
                source="RSS",
            )
            new_count += 1

        if new_count > 0:
            _logger.info("poll_complete", new_items=new_count)

        # Trim seen set to prevent unbounded growth
        if len(self._seen) > 10000:
            excess = len(self._seen) - 5000
            for _ in range(excess):
                self._seen.pop()

    async def _score_headline(self, headline: str) -> Any:
        """Score a single headline through the sentiment scorer."""
        now = datetime.now(UTC)
        event = UnifiedNewsEvent(
            event_id="poll-temp",
            schema_version=1,
            source=SourceType.RSS,
            headline=headline,
            body=None,
            published_at=now,
            symbols=[],
            raw_sentiment=0.0,
            sentiment_source=SentimentSource.NONE,
            ingested_at=now,
        )
        article = ArticleInput(article_text=headline, headline=headline)
        context = AnalysisContext(article=article, event=event)
        return await self._scorer.score(context)

    async def _fetch_rss(self) -> list[dict[str, Any]]:
        """Fetch entries from all configured RSS feeds."""
        try:
            import feedparser  # type: ignore[import-untyped]
        except ImportError:
            _logger.warning("feedparser_not_installed")
            return []
        entries: list[dict[str, Any]] = []
        for url in self._rss_urls:
            try:
                feed = await asyncio.to_thread(feedparser.parse, url)
                raw_entries = getattr(feed, "entries", [])
                for entry in raw_entries[:10]:
                    entries.append(dict(entry) if not isinstance(entry, dict) else entry)
            except (OSError, RuntimeError, ValueError) as exc:
                _logger.debug("rss_fetch_error", url=url, error=str(exc))
        return entries
