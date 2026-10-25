from __future__ import annotations

import asyncio
from importlib import import_module
from typing import Any

import structlog

PIPELINE_TASK = "sentiment-analysis"
MODEL_NAME = "ProsusAI/finbert"
POSITIVE_LABEL = "POSITIVE"
NEGATIVE_LABEL = "NEGATIVE"
NEUTRAL_LABEL = "NEUTRAL"


class FinBERTEnricher:
    def __init__(self) -> None:
        """Initializes the lazy FinBERT enricher."""
        self._pipeline: Any | None = None
        self._load_failed = False
        self._lock = asyncio.Lock()
        self._logger = structlog.get_logger().bind(component=self.__class__.__name__)

    async def score(self, text: str) -> float:
        """Returns a FinBERT sentiment score for the provided text."""
        pipeline = await self._get_pipeline()
        if pipeline is None:
            return 0.0

        try:
            result = await asyncio.to_thread(pipeline, text)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self._logger.warning("finbert_inference_failed", error=str(error), exc_info=True)
            return 0.0

        if not isinstance(result, list) or not result:
            return 0.0
        if not isinstance(result[0], dict):
            return 0.0

        label = result[0].get("label")
        confidence = result[0].get("score")
        if not isinstance(label, str) or not isinstance(confidence, int | float):
            return 0.0

        normalized_label = label.upper()
        if normalized_label == POSITIVE_LABEL:
            return float(confidence)
        if normalized_label == NEGATIVE_LABEL:
            return -float(confidence)
        if normalized_label == NEUTRAL_LABEL:
            return 0.0
        return 0.0

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
                self._pipeline = await asyncio.to_thread(self._load_pipeline)
            except (AttributeError, ImportError, OSError, RuntimeError, ValueError) as error:
                self._logger.warning("finbert_model_load_failed", error=str(error), exc_info=True)
                self._load_failed = True
                return None
            return self._pipeline

    def _load_pipeline(self) -> Any:
        transformers = import_module("transformers")
        pipeline_factory = getattr(transformers, "pipeline")
        return pipeline_factory(PIPELINE_TASK, model=MODEL_NAME)
