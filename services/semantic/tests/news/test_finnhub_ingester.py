from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import SecretStr

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

import news.ingestion.finnhub_ingester as finnhub_ingester_module
import news.ingestion.rate_limiter as rate_limiter_module
from news.ingestion.config import FinnhubConfig
from news.ingestion.finnhub_ingester import FinnhubIngester
from news.ingestion.rate_limiter import TokenBucketRateLimiter

FINNHUB_GENERAL_NEWS_URL = "https://finnhub.io/api/v1/news"


@pytest.mark.asyncio
@respx.mock
async def test_finnhub_ingester_deduplicates_article_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        [{"id": 101, "headline": "EUR rallies"}],
        [{"id": 101, "headline": "EUR rallies duplicate"}],
        [{"id": 202, "headline": "USD softens"}],
    ]
    call_index = 0

    async def fake_sleep(delay: float) -> None:
        del delay

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_index
        response_index = min(call_index, len(responses) - 1)
        call_index += 1
        assert request.url.params["category"] == "general"
        assert request.url.params["token"] == "demo-key"
        return httpx.Response(200, json=responses[response_index], request=request)

    monkeypatch.setattr(finnhub_ingester_module.asyncio, "sleep", fake_sleep)
    route = respx.get(FINNHUB_GENERAL_NEWS_URL).mock(side_effect=handler)

    config = FinnhubConfig(
        api_key=SecretStr("demo-key"),
        company_news_symbols=[],
        poll_interval_seconds=0,
    )

    async with FinnhubIngester(config, TokenBucketRateLimiter(config.rate_limit_per_minute)) as ingester:
        stream = ingester.ingest()
        first_article = await anext(stream)
        second_article = await anext(stream)
        await stream.aclose()

    assert [first_article["id"], second_article["id"]] == [101, 202]
    assert route.call_count == 3


@pytest.mark.asyncio
async def test_token_bucket_rate_limiter_consumes_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    current_time = 0.0
    limiter = TokenBucketRateLimiter(calls_per_minute=2)

    async def fake_sleep(delay: float) -> None:
        nonlocal current_time
        current_time += delay

    monkeypatch.setattr(rate_limiter_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(limiter, "_now", lambda: current_time)

    async with limiter:
        pass

    async with limiter:
        pass

    assert len(limiter._token_timestamps) == 2

    async with limiter:
        pass

    assert current_time == 60.0
    assert len(limiter._token_timestamps) == 1


@pytest.mark.asyncio
@respx.mock
async def test_finnhub_ingester_retries_after_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    call_count = 0

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 4:
            return httpx.Response(429, request=request)
        return httpx.Response(200, json=[{"id": 303, "headline": "Fed holds"}], request=request)

    monkeypatch.setattr(finnhub_ingester_module.asyncio, "sleep", fake_sleep)
    respx.get(FINNHUB_GENERAL_NEWS_URL).mock(side_effect=handler)

    config = FinnhubConfig(
        api_key=SecretStr("demo-key"),
        company_news_symbols=[],
        poll_interval_seconds=0,
        max_retries=5,
    )

    async with FinnhubIngester(config, TokenBucketRateLimiter(config.rate_limit_per_minute)) as ingester:
        stream = ingester.ingest()
        article = await anext(stream)
        await stream.aclose()

    assert article["id"] == 303
    assert call_count == 4
    assert sleep_calls == [2, 4, 8]
