from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

import news.ingestion.reddit_ingester as reddit_ingester_module
from news.ingestion.reddit_config import RedditConfig
from news.ingestion.reddit_ingester import RedditIngester


async def run_to_thread_immediately(func: object, *args: object, **kwargs: object) -> object:
    if not callable(func):
        raise TypeError("Expected a callable for asyncio.to_thread().")
    return func(*args, **kwargs)


class FakeSubreddit:
    def __init__(self, posts_by_call: list[list[SimpleNamespace]]) -> None:
        self._posts_by_call = posts_by_call
        self._call_count = 0

    def hot(self, limit: int) -> list[SimpleNamespace]:
        del limit
        index = min(self._call_count, len(self._posts_by_call) - 1)
        self._call_count += 1
        return self._posts_by_call[index]


class FakeReddit:
    def __init__(self, subreddits: dict[str, FakeSubreddit]) -> None:
        self._subreddits = subreddits
        self.read_only = False
        self.closed = False

    def subreddit(self, name: str) -> FakeSubreddit:
        return self._subreddits[name]

    def close(self) -> None:
        self.closed = True


def make_post(
    post_id: str,
    subreddit_name: str,
    score: int,
    title: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=post_id,
        title=title or f"Post {post_id}",
        selftext=f"Body {post_id}",
        url=f"https://reddit.test/{post_id}",
        subreddit=SimpleNamespace(display_name=subreddit_name),
        score=score,
        created_utc=1_710_000_000.0,
    )


@pytest.mark.asyncio
async def test_reddit_ingester_filters_low_score_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    created_clients: list[FakeReddit] = []

    async def fake_sleep(delay: float) -> None:
        del delay

    def fake_reddit_constructor(**kwargs: object) -> FakeReddit:
        del kwargs
        client = FakeReddit(
            {
                "wallstreetbets": FakeSubreddit(
                    [
                        [
                            make_post("low-score", "wallstreetbets", 5),
                            make_post("high-score-1", "wallstreetbets", 15),
                            make_post("high-score-2", "wallstreetbets", 40),
                        ],
                    ],
                ),
            },
        )
        created_clients.append(client)
        return client

    monkeypatch.setattr(reddit_ingester_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(reddit_ingester_module.asyncio, "to_thread", run_to_thread_immediately)
    monkeypatch.setattr(reddit_ingester_module.praw, "Reddit", fake_reddit_constructor)

    config = RedditConfig(
        client_id=SecretStr("client-id"),
        client_secret=SecretStr("client-secret"),
        subreddits=["wallstreetbets"],
        post_limit=25,
        poll_interval_seconds=0,
        min_score=10,
    )

    async with RedditIngester(config) as ingester:
        stream = ingester.ingest()
        first_post = await anext(stream)
        second_post = await anext(stream)
        await stream.aclose()

    assert [first_post["id"], second_post["id"]] == ["high-score-1", "high-score-2"]
    assert created_clients[0].read_only is True
    assert created_clients[0].closed is True


@pytest.mark.asyncio
async def test_reddit_ingester_deduplicates_post_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(delay: float) -> None:
        del delay

    def fake_reddit_constructor(**kwargs: object) -> FakeReddit:
        del kwargs
        return FakeReddit(
            {
                "forex": FakeSubreddit(
                    [
                        [make_post("duplicate-post", "forex", 25)],
                        [make_post("duplicate-post", "forex", 30)],
                        [make_post("new-post", "forex", 22)],
                    ],
                ),
            },
        )

    monkeypatch.setattr(reddit_ingester_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(reddit_ingester_module.asyncio, "to_thread", run_to_thread_immediately)
    monkeypatch.setattr(reddit_ingester_module.praw, "Reddit", fake_reddit_constructor)

    config = RedditConfig(
        client_id=SecretStr("client-id"),
        client_secret=SecretStr("client-secret"),
        subreddits=["forex"],
        post_limit=25,
        poll_interval_seconds=0,
        min_score=10,
    )

    async with RedditIngester(config) as ingester:
        stream = ingester.ingest()
        first_post = await anext(stream)
        second_post = await anext(stream)
        await stream.aclose()

    assert [first_post["id"], second_post["id"]] == ["duplicate-post", "new-post"]
