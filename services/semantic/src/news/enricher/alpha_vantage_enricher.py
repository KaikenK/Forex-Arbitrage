from __future__ import annotations

import httpx
from pydantic import SecretStr
import structlog

from .budget import AlphaVantageBudget

ALPHA_VANTAGE_ENDPOINT = "https://www.alphavantage.co/query"
FUNCTION_PARAM = "function"
FUNCTION_VALUE = "NEWS_SENTIMENT"
TICKERS_PARAM = "tickers"
API_KEY_PARAM = "apikey"
LIMIT_PARAM = "limit"
LIMIT_VALUE = "1"
SORT_PARAM = "sort"
SORT_VALUE = "LATEST"
FEED_KEY = "feed"
TITLE_KEY = "title"
SENTIMENT_SCORE_KEY = "overall_sentiment_score"


class AlphaVantageEnricher:
    def __init__(self, api_key: SecretStr, budget: AlphaVantageBudget) -> None:
        """Initializes the Alpha Vantage enricher."""
        self._api_key = api_key
        self._budget = budget
        self._logger = structlog.get_logger().bind(component=self.__class__.__name__)

    async def enrich_score(self, symbol: str, headline: str) -> float | None:
        """Returns an Alpha Vantage sentiment score when budget and request allow it."""
        if not self._api_key.get_secret_value():
            return None
        if not await self._budget.can_call():
            return None

        await self._budget.record_call()

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    ALPHA_VANTAGE_ENDPOINT,
                    params={
                        FUNCTION_PARAM: FUNCTION_VALUE,
                        TICKERS_PARAM: symbol,
                        API_KEY_PARAM: self._api_key.get_secret_value(),
                        LIMIT_PARAM: LIMIT_VALUE,
                        SORT_PARAM: SORT_VALUE,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError:
            self._logger.warning(
                "alpha_vantage_request_failed",
                symbol=symbol,
                headline=headline,
                exc_info=True,
            )
            return None

        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None

        feed = payload.get(FEED_KEY)
        if not isinstance(feed, list):
            return None

        target_headline = headline.casefold()
        for article in feed:
            if not isinstance(article, dict):
                continue

            article_title = article.get(TITLE_KEY)
            if not isinstance(article_title, str) or article_title.casefold() != target_headline:
                continue

            raw_score = article.get(SENTIMENT_SCORE_KEY)
            if not isinstance(raw_score, int | float | str):
                return None
            try:
                return self._normalize_score(float(raw_score))
            except (TypeError, ValueError):
                return None

        return None

    def _normalize_score(self, raw_score: float) -> float:
        normalized = (raw_score * 2.0) - 1.0
        return max(-1.0, min(1.0, normalized))

    def get_budget_status(self) -> dict[str, int | str]:
        """Returns the current Alpha Vantage budget status."""
        return self._budget.get_budget_status()
