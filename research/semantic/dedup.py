"""Syndication dedup: collapse the same story into one canonical event + a coverage count.

A wire story is republished across many outlets, so the raw feed counts one event dozens of
times. That inflates apparent sample size and biases the event study (the same shock enters the
regression repeatedly). Collapsing them yields two things:

  - one canonical event per story (correct unit of observation);
  - `coverage_count`, i.e. how many outlets carried it, which is a free *attention* proxy and a
    candidate predictor of reaction magnitude in its own right.

Similarity uses embeddings so rewordings by different outlets still collapse, unlike exact
string matching.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class CanonicalEvent:
    event_id: str                    # representative (earliest) event
    timestamp: datetime
    headline: str
    coverage_count: int              # outlets carrying this story
    member_ids: tuple[str, ...]
    distinct_domains: int


def deduplicate(
    event_ids: Sequence[str],
    timestamps: Sequence[datetime],
    headlines: Sequence[str],
    embeddings: np.ndarray,
    *,
    domains: Sequence[str] | None = None,
    window_hours: float = 24.0,
    similarity_threshold: float = 0.85,
) -> list[CanonicalEvent]:
    """Greedy single-pass clustering in time order; the earliest story wins as canonical."""
    n = len(event_ids)
    if n == 0:
        return []
    domains = list(domains) if domains is not None else [""] * n
    order = sorted(range(n), key=lambda i: timestamps[i])
    window = timedelta(hours=window_hours)

    cluster_of: dict[int, int] = {}
    heads: list[int] = []           # indices (into `order` space) of cluster representatives
    members: dict[int, list[int]] = {}

    for position in order:
        vector = embeddings[position]
        current_time = timestamps[position]
        assigned = None
        # Compare only against cluster heads still inside the time window.
        for head in reversed(heads):
            if current_time - timestamps[head] > window:
                break
            if float(embeddings[head] @ vector) >= similarity_threshold:
                assigned = head
                break
        if assigned is None:
            heads.append(position)
            members[position] = [position]
            cluster_of[position] = position
        else:
            members[assigned].append(position)
            cluster_of[position] = assigned

    canonical: list[CanonicalEvent] = []
    for head in heads:
        group = members[head]
        canonical.append(
            CanonicalEvent(
                event_id=str(event_ids[head]),
                timestamp=timestamps[head],
                headline=str(headlines[head]),
                coverage_count=len(group),
                member_ids=tuple(str(event_ids[i]) for i in group),
                distinct_domains=len({domains[i] for i in group if domains[i]}),
            )
        )
    canonical.sort(key=lambda item: item.timestamp)
    return canonical
