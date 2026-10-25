from __future__ import annotations

from importlib import import_module
from typing import Any


class _NoOpMetric:
    def labels(self, *args: object, **kwargs: object) -> _NoOpMetric:
        """Returns the same no-op metric for labeled calls."""
        del args
        del kwargs
        return self

    def inc(self, amount: float = 1.0) -> None:
        """Ignores counter increments when metrics are unavailable."""
        del amount

    def set(self, value: float) -> None:
        """Ignores gauge updates when metrics are unavailable."""
        del value


try:
    prometheus_client = import_module("prometheus_client")
    counter = getattr(prometheus_client, "Counter")
    gauge = getattr(prometheus_client, "Gauge")

    events_ingested_total: Any = counter(
        "events_ingested_total",
        "Count of raw events ingested.",
        ["source"],
    )
    events_normalized_total: Any = counter(
        "events_normalized_total",
        "Count of normalization attempts by source and status.",
        ["source", "status"],
    )
    sentiment_signals_total: Any = counter(
        "sentiment_signals_total",
        "Count of sentiment signals emitted by symbol and context tag.",
        ["symbol", "context_tag"],
    )
    av_budget_remaining: Any = gauge(
        "av_budget_remaining",
        "Remaining Alpha Vantage calls for the current UTC day.",
    )
    queue_depth: Any = gauge(
        "queue_depth",
        "Current depth of the news queue.",
    )
except ImportError:
    events_ingested_total = _NoOpMetric()
    events_normalized_total = _NoOpMetric()
    sentiment_signals_total = _NoOpMetric()
    av_budget_remaining = _NoOpMetric()
    queue_depth = _NoOpMetric()
