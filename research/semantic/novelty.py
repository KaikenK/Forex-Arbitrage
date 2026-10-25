"""Semantic novelty: how much genuinely new information a headline carries.

The N1 hypothesis is that markets react to *new* information rather than to *positive* or
*negative* information, so reaction magnitude should track novelty better than sentiment.

Novelty is measured against the recent news manifold: for an event at time t, compare its
embedding to the embeddings of events in the trailing window [t - lookback, t). Three views:

  max_similarity   - closest prior story. Near 1.0 means "this was already reported".
  topk_similarity  - mean of the k closest prior stories, robust to a single odd match.
  centroid_similarity - similarity to the average of recent news, i.e. how on-theme it is.

novelty = 1 - topk_similarity, so higher means more surprising.

Leak-safety: the window is strictly *prior* (t_j < t_i). Ties at the same timestamp are excluded
because GDELT quantizes to 15-minute buckets, so a same-bucket story is not knowably earlier.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class NoveltyScore:
    event_id: str
    novelty: float | None            # 1 - topk_similarity (None when no prior context)
    max_similarity: float | None
    topk_similarity: float | None
    centroid_similarity: float | None
    context_size: int                # number of prior events compared against

    def to_row(self) -> dict:
        return {
            "event_id": self.event_id,
            "novelty": self.novelty,
            "max_similarity": self.max_similarity,
            "topk_similarity": self.topk_similarity,
            "centroid_similarity": self.centroid_similarity,
            "novelty_context_size": self.context_size,
        }


def compute_novelty(
    event_ids: Sequence[str],
    timestamps: Sequence[datetime],
    embeddings: np.ndarray,
    *,
    lookback_hours: float = 168.0,   # 7 days of news context
    top_k: int = 5,
    min_context: int = 3,
) -> list[NoveltyScore]:
    """Score each event's novelty against the strictly-prior trailing window.

    Inputs need not be sorted. Embeddings must be L2-normalized (all `Embedder`s return them
    that way), so cosine similarity is a dot product.
    """
    n = len(event_ids)
    if n == 0:
        return []
    if len(timestamps) != n or embeddings.shape[0] != n:
        raise ValueError("event_ids, timestamps, and embeddings must have the same length")

    order = sorted(range(n), key=lambda i: timestamps[i])
    sorted_times = [timestamps[i] for i in order]
    sorted_vectors = embeddings[order]
    lookback = timedelta(hours=lookback_hours)

    scores: list[NoveltyScore | None] = [None] * n
    for position, index in enumerate(order):
        current_time = sorted_times[position]
        # Strictly-prior window: [current - lookback, current). bisect_left excludes ties,
        # which matters because quantized timestamps make same-bucket ordering unknowable.
        left = bisect.bisect_left(sorted_times, current_time - lookback)
        right = bisect.bisect_left(sorted_times, current_time)
        context = sorted_vectors[left:right]

        if context.shape[0] < min_context:
            scores[index] = NoveltyScore(
                event_id=event_ids[index], novelty=None, max_similarity=None,
                topk_similarity=None, centroid_similarity=None, context_size=context.shape[0],
            )
            continue

        similarities = context @ sorted_vectors[position]
        k = min(top_k, similarities.shape[0])
        top_values = np.partition(similarities, -k)[-k:]
        topk_similarity = float(np.mean(top_values))
        centroid = context.mean(axis=0)
        centroid_norm = np.linalg.norm(centroid)
        centroid_similarity = (
            float(centroid @ sorted_vectors[position] / centroid_norm) if centroid_norm > 0 else 0.0
        )
        scores[index] = NoveltyScore(
            event_id=event_ids[index],
            novelty=1.0 - topk_similarity,
            max_similarity=float(np.max(similarities)),
            topk_similarity=topk_similarity,
            centroid_similarity=centroid_similarity,
            context_size=context.shape[0],
        )
    return [score for score in scores if score is not None]
