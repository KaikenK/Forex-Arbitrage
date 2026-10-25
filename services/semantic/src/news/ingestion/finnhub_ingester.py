from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator
from datetime import UTC
from datetime import datetime
from types import TracebackType
from typing import Any
from typing import Self

import httpx
import structlog

from ..interfaces import NewsIngester
from .config import FinnhubConfig
from .rate_limiter import TokenBucketRateLimiter

GENERAL_NEWS_ENDPOINT = "news"
COMPANY_NEWS_ENDPOINT = "company-news"
CATEGORY_PARAM = "category"
SYMBOL_PARAM = "symbol"
FROM_DATE_PARAM = "from"
TO_DATE_PARAM = "to"
TOKEN_PARAM = "token"
ARTICLE_ID_KEY = "id"
MAX_DEDUP_ENTRIES = 50_000
EVICTION_PERCENTAGE = 0.10
BACKOFF_BASE_SECONDS = 2
BACKOFF_MAX_SECONDS = 60


def utc_now() -> datetime:
    return datetime.now(UTC)


class FinnhubIngester(NewsIngester):
    def __init__(self, config: FinnhubConfig, rate_limiter: TokenBucketRateLimiter) -> None:
        """Initializes the Finnhub ingester."""
        self.config = config
        self._rate_limiter = rate_limiter
        self._client: httpx.AsyncClient | None = None
        self._seen_article_ids: OrderedDict[str, None] = OrderedDict()
        self._last_successful_ingest_at: datetime | None = None
        self._logger = structlog.get_logger().bind(component=self.__class__.__name__)

    async def __aenter__(self) -> Self:
        """Creates the async HTTP client."""
        self._client = httpx.AsyncClient(base_url=self.config.base_url, timeout=self.config.timeout_seconds)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Closes the async HTTP client."""
        del exc_type
        del exc
        del traceback

        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def ingest(self) -> AsyncIterator[dict]:
        """Yields deduplicated raw Finnhub news payloads."""
        while True:
            general_news = await self._fetch_general_news()
            for article in self._yield_new_articles(general_news):
                yield article

            for symbol in self.config.company_news_symbols:
                company_news = await self._fetch_company_news(symbol)
                for article in self._yield_new_articles(company_news):
                    yield article

            self._last_successful_ingest_at = utc_now()
            await asyncio.sleep(self.config.poll_interval_seconds)

    def is_healthy(self) -> bool:
        """Returns whether the Finnhub ingester is healthy."""
        if self._last_successful_ingest_at is None:
            return False
        max_age_seconds = 2 * self.config.poll_interval_seconds
        age_seconds = (utc_now() - self._last_successful_ingest_at).total_seconds()
        return age_seconds <= max_age_seconds

    async def _fetch_general_news(self) -> list[dict[str, Any]]:
        return await self._request_articles(
            GENERAL_NEWS_ENDPOINT,
            {CATEGORY_PARAM: self.config.general_news_category},
        )

    async def _fetch_company_news(self, symbol: str) -> list[dict[str, Any]]:
        current_date = self._utc_today()
        return await self._request_articles(
            COMPANY_NEWS_ENDPOINT,
            {
                SYMBOL_PARAM: symbol,
                FROM_DATE_PARAM: current_date,
                TO_DATE_PARAM: current_date,
            },
        )

    async def _request_articles(self, endpoint: str, params: dict[str, str]) -> list[dict[str, Any]]:
        request_params = dict(params)
        request_params[TOKEN_PARAM] = self.config.api_key.get_secret_value()

        for attempt in range(1, self.config.max_retries + 1):
            try:
                async with self._rate_limiter:
                    response = await self._get_client().get(endpoint, params=request_params)
                response.raise_for_status()
            except httpx.HTTPError:
                if attempt == self.config.max_retries:
                    raise

                await asyncio.sleep(self._backoff_seconds(attempt))
                continue

            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("Expected Finnhub to return a list of article dictionaries.")

            articles: list[dict[str, Any]] = []
            for raw_article in payload:
                if not isinstance(raw_article, dict):
                    raise ValueError("Expected each Finnhub article to be a dictionary.")
                articles.append(raw_article)
            return articles

        raise RuntimeError("Retry loop exited without returning Finnhub articles.")

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Finnhub client is not initialized. Use an async context manager.")
        return self._client

    def _yield_new_articles(self, articles: list[dict[str, Any]]) -> list[dict]:
        new_articles: list[dict] = []
        for article in articles:
            article_id = article.get(ARTICLE_ID_KEY)
            if article_id is None:
                raise ValueError("Expected each Finnhub article to contain an id field.")

            if self._remember_article_id(str(article_id)):
                new_articles.append(article)
        return new_articles

    def _remember_article_id(self, article_id: str) -> bool:
        if article_id in self._seen_article_ids:
            return False

        if len(self._seen_article_ids) >= MAX_DEDUP_ENTRIES:
            self._evict_oldest_article_ids()

        self._seen_article_ids[article_id] = None
        return True

    def _evict_oldest_article_ids(self) -> None:
        eviction_count = max(1, int(MAX_DEDUP_ENTRIES * EVICTION_PERCENTAGE))
        for _ in range(eviction_count):
            if not self._seen_article_ids:
                break
            self._seen_article_ids.popitem(last=False)

    def _backoff_seconds(self, attempt: int) -> int:
        return min(BACKOFF_BASE_SECONDS**attempt, BACKOFF_MAX_SECONDS)

    def _utc_today(self) -> str:
        return datetime.now(UTC).date().isoformat()
