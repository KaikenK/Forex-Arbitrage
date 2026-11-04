from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from typing import Any

import structlog

PIPELINE_TASK = "sentiment-analysis"
MODEL_NAME = "ProsusAI/finbert"
POSITIVE_LABEL = "POSITIVE"
NEGATIVE_LABEL = "NEGATIVE"
NEUTRAL_LABEL = "NEUTRAL"
MAX_TOKENS = 128          # headlines and summaries fit; longer text used to raise and score 0
TORCH_THREADS = 4         # 4 threads: ~29 ms per headline vs 26 ms at 16, leaving cores for the rest
CACHE_SIZE = 2048         # the SSE path and the poller score the same headline text


Probabilities = tuple[float, float, float]      # P(positive), P(negative), P(neutral)


class FinBERTEnricher:
    def __init__(self, *, max_tokens: int = MAX_TOKENS, torch_threads: int = TORCH_THREADS,
                 cache_size: int = CACHE_SIZE) -> None:
        """Initializes the lazy FinBERT enricher.

        Inference runs on one dedicated worker thread, so concurrent callers queue instead
        of competing for the CPU, and results are cached by exact text.
        """
        self._pipeline: Any | None = None
        self._load_failed = False
        self._lock = asyncio.Lock()
        self._max_tokens = max_tokens
        self._torch_threads = torch_threads
        self._cache: OrderedDict[str, Probabilities] = OrderedDict()
        self._cache_size = cache_size
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="finbert")
        self._failures = 0
        self._logger = structlog.get_logger().bind(component=self.__class__.__name__)

    async def probabilities(self, text: str) -> Probabilities | None:
        """(P(positive), P(negative), P(neutral)) for `text`, or None if the model is unavailable
        or inference failed."""
        key = text.strip()
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        pipeline = await self._get_pipeline()
        if pipeline is None:
            return None
        try:
            result = await asyncio.get_running_loop().run_in_executor(self._executor, self._infer, pipeline, key)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self._failures += 1
            if self._failures == 1 or self._failures % 100 == 0:
                self._logger.warning("finbert_inference_failed", error=str(error), failures=self._failures)
            return None
        probabilities = label_probabilities(result)
        if probabilities is None:
            return None
        self._cache[key] = probabilities
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return probabilities

    async def score(self, text: str) -> float:
        """Expected polarity P(positive) - P(negative) in [-1, 1]; 0.0 when unavailable."""
        probabilities = await self.probabilities(text)
        return 0.0 if probabilities is None else probabilities[0] - probabilities[1]

    async def warm_up(self) -> float | None:
        """Loads the model and runs one inference; returns the seconds taken, or None on failure."""
        started = time.perf_counter()
        if await self._get_pipeline() is None:
            return None
        await self.probabilities("Warm-up: the dollar was steady.")
        self._cache.clear()
        return time.perf_counter() - started

    def _infer(self, pipeline: Any, text: str) -> Any:
        return pipeline(text, top_k=None, truncation=True, max_length=self._max_tokens)

    async def _get_pipeline(self) -> Any | None:
        if self._pipeline is not None:
            return self._pipeline
        if self._load_failed:
            return None

        async with self._lock:
            if self._pipeline is not None:
                return self._pipeline
            if self._load_failed:
                return None

            try:
                self._pipeline = await asyncio.get_running_loop().run_in_executor(self._executor, self._load_pipeline)
            except (AttributeError, ImportError, OSError, RuntimeError, ValueError) as error:
                self._logger.warning("finbert_model_load_failed", error=str(error))
                self._load_failed = True
                return None
            return self._pipeline

    def _load_pipeline(self) -> Any:
        try:
            import_module("torch").set_num_threads(self._torch_threads)
        except (ImportError, AttributeError, RuntimeError):
            pass
        transformers = import_module("transformers")
        pipeline_factory = getattr(transformers, "pipeline")
        return pipeline_factory(PIPELINE_TASK, model=MODEL_NAME)


def label_probabilities(result: Any) -> Probabilities | None:
    """Reads the three FinBERT label probabilities from a `top_k=None` pipeline result."""
    if isinstance(result, list) and result and isinstance(result[0], list):
        result = result[0]                                    # some versions nest one level
    if not isinstance(result, list):
        return None
    scores: dict[str, float] = {}
    for item in result:
        if isinstance(item, dict) and isinstance(item.get("label"), str) and isinstance(item.get("score"), int | float):
            scores[item["label"].upper()] = float(item["score"])
    if not {POSITIVE_LABEL, NEGATIVE_LABEL, NEUTRAL_LABEL} <= scores.keys():
        return None
    return scores[POSITIVE_LABEL], scores[NEGATIVE_LABEL], scores[NEUTRAL_LABEL]


_shared: FinBERTEnricher | None = None


def shared_enricher() -> FinBERTEnricher:
    """The process-wide enricher: one model in memory however many scorers use it."""
    global _shared
    if _shared is None:
        _shared = FinBERTEnricher()
    return _shared
