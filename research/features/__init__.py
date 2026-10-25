"""Canonical event-level feature table assembly."""
from .context import build_market_context_fn, session_features
from .assemble import (
    SEMANTIC_COLUMNS,
    attach_semantic_features,
    build_feature_table,
    compute_event_semantics,
    events_to_records,
    pair_news_currency,
    store_df_to_records,
)

__all__ = [
    "build_market_context_fn",
    "session_features",
    "build_feature_table",
    "events_to_records",
    "store_df_to_records",
    "SEMANTIC_COLUMNS",
    "attach_semantic_features",
    "compute_event_semantics",
    "pair_news_currency",
]
