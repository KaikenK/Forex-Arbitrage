from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

import news.ingestion.rss_ingester as rss_ingester_module
from news.ingestion.rss_config import RSSConfig
from news.ingestion.rss_ingester import RSSIngester


async def run_to_thread_immediately(func: object, *args: object, **kwargs: object) -> object:
    if not callable(func):
        raise TypeError("Expected a callable for asyncio.to_thread().")
    return func(*args, **kwargs)


@pytest.mark.asyncio
async def test_rss_ingester_yields_entries_from_successful_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(delay: float) -> None:
        del delay

    def fake_parse(feed_url: str) -> SimpleNamespace:
        assert feed_url == "https://example.com/feed"
        return SimpleNamespace(
            entries=[
                {"link": "https://example.com/article-1", "title": "Entry 1"},
                {"link": "https://example.com/article-2", "title": "Entry 2"},
                {"link": "https://example.com/article-3", "title": "Entry 3"},
            ],
            bozo=False,
        )

    monkeypatch.setattr(rss_ingester_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(rss_ingester_module.asyncio, "to_thread", run_to_thread_immediately)
    monkeypatch.setattr(rss_ingester_module.feedparser, "parse", fake_parse)

    ingester = RSSIngester(
        RSSConfig(feed_urls=["https://example.com/feed"], poll_interval_seconds=0, max_entries_per_feed=20),
    )

    stream = ingester.ingest()
    entries = [await anext(stream) for _ in range(3)]
    await stream.aclose()

    assert [entry["link"] for entry in entries] == [
        "https://example.com/article-1",
        "https://example.com/article-2",
        "https://example.com/article-3",
    ]


@pytest.mark.asyncio
async def test_rss_ingester_continues_when_one_feed_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(delay: float) -> None:
        del delay

    def fake_parse(feed_url: str) -> SimpleNamespace:
        if feed_url == "https://example.com/bad-feed":
            raise RuntimeError("Feed unavailable")
        return SimpleNamespace(
            entries=[
                {"link": "https://example.com/good-article-1", "title": "Good Entry 1"},
                {"link": "https://example.com/good-article-2", "title": "Good Entry 2"},
            ],
            bozo=False,
        )

    monkeypatch.setattr(rss_ingester_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(rss_ingester_module.asyncio, "to_thread", run_to_thread_immediately)
    monkeypatch.setattr(rss_ingester_module.feedparser, "parse", fake_parse)

    ingester = RSSIngester(
        RSSConfig(
            feed_urls=["https://example.com/bad-feed", "https://example.com/good-feed"],
            poll_interval_seconds=0,
            max_entries_per_feed=20,
        ),
    )

    stream = ingester.ingest()
    entries = [await anext(stream) for _ in range(2)]
    await stream.aclose()

    assert [entry["link"] for entry in entries] == [
        "https://example.com/good-article-1",
        "https://example.com/good-article-2",
    ]


@pytest.mark.asyncio
async def test_rss_ingester_deduplicates_across_poll_cycles(monkeypatch: pytest.MonkeyPatch) -> None:
    parse_count = 0

    async def fake_sleep(delay: float) -> None:
        del delay

    def fake_parse(feed_url: str) -> SimpleNamespace:
        nonlocal parse_count
        assert feed_url == "https://example.com/feed"
        parse_count += 1

        if parse_count == 1:
            entries = [{"link": "https://example.com/article-1", "title": "Entry 1"}]
        elif parse_count == 2:
            entries = [{"link": "https://example.com/article-1", "title": "Entry 1 duplicate"}]
        else:
            entries = [{"link": "https://example.com/article-2", "title": "Entry 2"}]

        return SimpleNamespace(entries=entries, bozo=False)

    monkeypatch.setattr(rss_ingester_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(rss_ingester_module.asyncio, "to_thread", run_to_thread_immediately)
    monkeypatch.setattr(rss_ingester_module.feedparser, "parse", fake_parse)

    ingester = RSSIngester(
        RSSConfig(feed_urls=["https://example.com/feed"], poll_interval_seconds=0, max_entries_per_feed=20),
    )

    stream = ingester.ingest()
    first_entry = await anext(stream)
    second_entry = await anext(stream)
    await stream.aclose()

    assert [first_entry["link"], second_entry["link"]] == [
        "https://example.com/article-1",
        "https://example.com/article-2",
    ]
    assert parse_count == 3
