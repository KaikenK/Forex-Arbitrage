from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from typing import Callable
from typing import cast

import httpx
import structlog

try:
    from prawcore.exceptions import PrawcoreException  # type: ignore[import-untyped]
except ImportError:
    PrawcoreException = RuntimeError

from .interfaces import NewsAnalyzer
from .interfaces import NewsIngester
from .interfaces import NewsNormalizer
from .interfaces import NewsQueue
from .interfaces import SentimentEnricher
from .metrics import events_ingested_total
from .metrics import events_normalized_total
from .models import SentimentSignal
from .models import SourceType
from .normalizer.exceptions import NormalizationError

INGESTER_RESTART_DELAY_SECONDS = 5
BUDGET_LOG_INTERVAL_SECONDS = 3600
NORMALIZATION_STATUS_OK = "ok"
NORMALIZATION_STATUS_ERROR = "error"
RECOVERABLE_INGESTER_EXCEPTIONS = (
    httpx.HTTPError,
    ImportError,
    OSError,
    PrawcoreException,
    RuntimeError,
    TimeoutError,
    TypeError,
    ValueError,
)


class NewsPipeline:
    def __init__(
        self,
        ingesters: list[NewsIngester],
        normalizers: dict[SourceType, NewsNormalizer],
        enricher: SentimentEnricher,
        queue: NewsQueue,
        analyzer: NewsAnalyzer,
        output_queue: asyncio.Queue[SentimentSignal],
        dead_letter: asyncio.Queue[dict],
    ) -> None:
        """Initializes the news pipeline dependencies."""
        self._ingesters = ingesters
        self._normalizers = normalizers
        self._enricher = enricher
        self._queue = queue
        self._analyzer = analyzer
        self._output_queue = output_queue
        self._dead_letter = dead_letter
        self._logger = structlog.get_logger().bind(component=self.__class__.__name__)
        self._stop_event = asyncio.Event()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._run_task: asyncio.Task[None] | None = None
        self._startup_config: dict[str, Any] | None = None
        self._budget_status_getter = self._resolve_budget_status_getter(enricher)

    async def run(self) -> None:
        """Runs the news pipeline until it is stopped."""
        self._stop_event.clear()
        self._run_task = asyncio.current_task()
        if self._startup_config is not None:
            self._logger.info("pipeline_starting", config=self._startup_config)

        try:
            async with asyncio.TaskGroup() as task_group:
                self._tasks = {
                    task_group.create_task(self._consume_queue(), name="news-pipeline-consumer"),
                }
                if self._budget_status_getter is not None:
                    self._tasks.add(
                        task_group.create_task(
                            self._log_budget_status_loop(),
                            name="news-pipeline-budget-status",
                        ),
                    )
                for index, ingester in enumerate(self._ingesters, start=1):
                    self._tasks.add(
                        task_group.create_task(
                            self._supervise_ingester(ingester),
                            name=f"news-pipeline-ingester-{index}",
                        ),
                    )

                await self._stop_event.wait()
        finally:
            self._tasks = set()
            self._run_task = None

    async def stop(self) -> None:
        """Stops the news pipeline and waits for cancellation."""
        self._stop_event.set()

        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        run_task = self._run_task
        current_task = asyncio.current_task()
        if run_task is not None and run_task is not current_task:
            await asyncio.gather(run_task, return_exceptions=True)

        self._logger.info("pipeline stopped cleanly")

    async def _supervise_ingester(self, ingester: NewsIngester) -> None:
        while not self._stop_event.is_set():
            try:
                await self._run_ingester_once(ingester)
                return
            except asyncio.CancelledError:
                raise
            except RECOVERABLE_INGESTER_EXCEPTIONS:
                self._logger.error(
                    "pipeline_ingester_failed",
                    ingester=type(ingester).__name__,
                    exc_info=True,
                )
                await asyncio.sleep(INGESTER_RESTART_DELAY_SECONDS)

    async def _run_ingester_once(self, ingester: NewsIngester) -> None:
        source_type = self._source_type_for_ingester(ingester)
        normalizer = self._normalizer_for(source_type)
        source_label = source_type.value.casefold()

        async with self._ingester_context(ingester) as active_ingester:
            async for raw in active_ingester.ingest():
                events_ingested_total.labels(source=source_label).inc()
                try:
                    event = normalizer.normalize(raw)
                except NormalizationError:
                    events_normalized_total.labels(source=source_label, status=NORMALIZATION_STATUS_ERROR).inc()
                    self._logger.error(
                        "pipeline_normalization_failed",
                        ingester=type(ingester).__name__,
                        source=source_label,
                        raw=raw,
                        exc_info=True,
                    )
                    await self._dead_letter.put(raw)
                    continue

                events_normalized_total.labels(source=source_label, status=NORMALIZATION_STATUS_OK).inc()
                enriched_event = await self._enricher.enrich(event)
                await self._queue.publish(enriched_event)

    async def _consume_queue(self) -> None:
        async for event in self._queue.subscribe():
            signals = await self._analyzer.analyze(event)
            for signal in signals:
                await self._output_queue.put(signal)

    async def _log_budget_status_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self._budget_status_getter is not None:
                    status = self._budget_status_getter()
                    self._logger.info("alpha_vantage_budget_status", **status)
            except (OSError, RuntimeError, TypeError, ValueError):
                self._logger.error("alpha_vantage_budget_status_failed", exc_info=True)

            await asyncio.sleep(BUDGET_LOG_INTERVAL_SECONDS)

    def _normalizer_for(self, source_type: SourceType) -> NewsNormalizer:
        normalizer = self._normalizers.get(source_type)
        if normalizer is None:
            raise ValueError(f"No normalizer registered for source type {source_type!s}.")
        return normalizer

    def _source_type_for_ingester(self, ingester: NewsIngester) -> SourceType:
        explicit_source = getattr(ingester, "source_type", getattr(ingester, "SOURCE_TYPE", None))
        if isinstance(explicit_source, SourceType):
            return explicit_source
        if isinstance(explicit_source, str):
            return SourceType[explicit_source.upper()]

        ingester_name = type(ingester).__name__.casefold()
        if "finnhub" in ingester_name:
            return SourceType.FINNHUB
        if "official" in ingester_name:
            return SourceType.OFFICIAL
        if "rss" in ingester_name:
            return SourceType.RSS
        if "reddit" in ingester_name:
            return SourceType.REDDIT

        raise ValueError(f"Unable to infer source type for ingester {type(ingester).__name__}.")

    @asynccontextmanager
    async def _ingester_context(self, ingester: NewsIngester) -> AsyncIterator[NewsIngester]:
        enter = getattr(ingester, "__aenter__", None)
        exit_method = getattr(ingester, "__aexit__", None)

        if callable(enter) and callable(exit_method):
            managed_ingester = cast(NewsIngester, await enter())
            try:
                yield managed_ingester
            finally:
                await exit_method(None, None, None)
            return

        yield ingester

    def _resolve_budget_status_getter(
        self,
        enricher: SentimentEnricher,
    ) -> Callable[[], dict[str, int | str]] | None:
        getter = getattr(enricher, "get_budget_status", None)
        if callable(getter):
            return cast(Callable[[], dict[str, int | str]], getter)
        return None
