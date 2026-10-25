from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import structlog

from ..interfaces import NewsQueue
from ..metrics import queue_depth
from ..models import UnifiedNewsEvent

DEFAULT_QUEUE_MAXSIZE = 10_000


class InMemoryNewsQueue(NewsQueue):
    def __init__(self, maxsize: int = DEFAULT_QUEUE_MAXSIZE) -> None:
        """Initializes the in-memory news queue."""
        self._queue: asyncio.Queue[UnifiedNewsEvent] = asyncio.Queue(maxsize=maxsize)
        self._logger = structlog.get_logger().bind(component=self.__class__.__name__)
        queue_depth.set(0.0)

    async def publish(self, event: UnifiedNewsEvent) -> None:
        """Publishes a unified event to the in-memory queue."""
        if self._queue.full():
            dropped_event = self._queue.get_nowait()
            self._queue.task_done()
            self._logger.warning(
                "news_queue_dropped_oldest",
                dropped_event_id=dropped_event.event_id,
                replacement_event_id=event.event_id,
            )

        await self._queue.put(event)
        queue_depth.set(float(self._queue.qsize()))

    async def subscribe(self) -> AsyncIterator[UnifiedNewsEvent]:
        """Yields unified events from the in-memory queue."""
        while True:
            event = await self._queue.get()
            queue_depth.set(float(self._queue.qsize()))
            try:
                yield event
            finally:
                self._queue.task_done()
