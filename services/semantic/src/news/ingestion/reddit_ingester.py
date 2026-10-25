from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator
from datetime import UTC
from datetime import datetime
from types import TracebackType
from typing import Any
from typing import Self

import praw  # type: ignore[import-untyped]
import structlog
from prawcore.exceptions import PrawcoreException  # type: ignore[import-untyped]

from ..interfaces import NewsIngester
from .reddit_config import RedditConfig

MAX_DEDUP_ENTRIES = 50_000
EVICTION_PERCENTAGE = 0.10


def utc_now() -> datetime:
    return datetime.now(UTC)


class RedditIngester(NewsIngester):
    def __init__(self, config: RedditConfig) -> None:
        """Initializes the Reddit ingester."""
        self.config = config
        self._logger = structlog.get_logger().bind(component=self.__class__.__name__)
        self._reddit: Any | None = None
        self._seen_post_ids: OrderedDict[str, None] = OrderedDict()
        self._last_successful_ingest_at: datetime | None = None

    async def __aenter__(self) -> Self:
        """Creates the read-only Reddit client."""
        self._reddit = await asyncio.to_thread(self._build_reddit_client)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Closes the Reddit client when supported."""
        del exc_type
        del exc
        del traceback

        if self._reddit is None:
            return

        close_method = getattr(self._reddit, "close", None)
        if callable(close_method):
            await asyncio.to_thread(close_method)

        self._reddit = None

    async def ingest(self) -> AsyncIterator[dict]:
        """Yields deduplicated raw Reddit post payloads."""
        while True:
            subreddit_batches = await asyncio.gather(
                *(self._fetch_subreddit_posts(subreddit_name) for subreddit_name in self.config.subreddits),
            )

            for posts in subreddit_batches:
                for post in posts:
                    yield post

            await asyncio.sleep(self.config.poll_interval_seconds)

    def _build_reddit_client(self) -> Any:
        reddit = praw.Reddit(
            client_id=self.config.client_id.get_secret_value(),
            client_secret=self.config.client_secret.get_secret_value(),
            user_agent=self.config.user_agent,
        )
        reddit.read_only = True
        return reddit

    async def _fetch_subreddit_posts(self, subreddit_name: str) -> list[dict[str, Any]]:
        try:
            posts = await asyncio.to_thread(self._list_subreddit_posts, subreddit_name)
            self._last_successful_ingest_at = utc_now()
            return posts
        except (OSError, RuntimeError, PrawcoreException) as error:
            self._logger.warning(
                "reddit_subreddit_fetch_failed",
                subreddit=subreddit_name,
                error=str(error),
                exc_info=True,
            )
            return []

    def _list_subreddit_posts(self, subreddit_name: str) -> list[dict[str, Any]]:
        subreddit = self._get_reddit().subreddit(subreddit_name)
        posts: list[dict[str, Any]] = []

        for post in subreddit.hot(limit=self.config.post_limit):
            if int(post.score) < self.config.min_score:
                continue

            post_id = str(post.id)
            if not self._remember_post_id(post_id):
                continue

            posts.append(
                {
                    "id": post_id,
                    "title": str(post.title),
                    "selftext": str(post.selftext),
                    "url": str(post.url),
                    "subreddit": self._subreddit_name(post.subreddit),
                    "score": int(post.score),
                    "created_utc": float(post.created_utc),
                },
            )
        return posts

    def _get_reddit(self) -> Any:
        if self._reddit is None:
            raise RuntimeError("Reddit client is not initialized. Use an async context manager.")
        return self._reddit

    def _subreddit_name(self, subreddit: Any) -> str:
        display_name = getattr(subreddit, "display_name", None)
        if display_name is not None:
            return str(display_name)
        return str(subreddit)

    def _remember_post_id(self, post_id: str) -> bool:
        if post_id in self._seen_post_ids:
            return False

        if len(self._seen_post_ids) >= MAX_DEDUP_ENTRIES:
            self._evict_oldest_post_ids()

        self._seen_post_ids[post_id] = None
        return True

    def _evict_oldest_post_ids(self) -> None:
        eviction_count = max(1, int(MAX_DEDUP_ENTRIES * EVICTION_PERCENTAGE))
        for _ in range(eviction_count):
            if not self._seen_post_ids:
                break
            self._seen_post_ids.popitem(last=False)

    def is_healthy(self) -> bool:
        """Returns whether the Reddit ingester is healthy."""
        if self._last_successful_ingest_at is None:
            return False
        max_age_seconds = 2 * self.config.poll_interval_seconds
        age_seconds = (utc_now() - self._last_successful_ingest_at).total_seconds()
        return age_seconds <= max_age_seconds
