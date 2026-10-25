from __future__ import annotations

import asyncio
from collections import deque
from types import TracebackType
from typing import Self

import structlog

WINDOW_SECONDS = 60.0
MIN_CALLS_PER_MINUTE = 1


class TokenBucketRateLimiter:
    def __init__(self, calls_per_minute: int) -> None:
        """Initializes the token bucket rate limiter."""
        self._calls_per_minute = max(calls_per_minute, MIN_CALLS_PER_MINUTE)
        self._token_timestamps: deque[float] = deque()
        self._logger = structlog.get_logger().bind(component=self.__class__.__name__)

    async def __aenter__(self) -> Self:
        """Waits until a request token is available."""
        while True:
            current_time = self._now()
            self._prune_expired_tokens(current_time)

            if len(self._token_timestamps) < self._calls_per_minute:
                self._token_timestamps.append(current_time)
                return self

            wait_seconds = max(WINDOW_SECONDS - (current_time - self._token_timestamps[0]), 0.0)
            await asyncio.sleep(wait_seconds)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Releases the rate limiter context."""
        del exc_type
        del exc
        del traceback

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    def _prune_expired_tokens(self, current_time: float) -> None:
        while self._token_timestamps and current_time - self._token_timestamps[0] >= WINDOW_SECONDS:
            self._token_timestamps.popleft()
