"""Tests for embeddings, leak-safe novelty scoring, and syndication dedup."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from research.semantic import (
    HashingEmbedder,
    TfidfSvdEmbedder,
    compute_novelty,
    deduplicate,
)


# ---------------------------------------------------------------- embedders

def test_embedders_return_l2_normalized_vectors():
    texts = ["ECB raises rates", "Fed holds rates steady", "oil prices surge on OPEC cuts"]
    for embedder in (HashingEmbedder(64), TfidfSvdEmbedder(dimensions=2).fit(texts)):
        vectors = embedder.encode(texts)
        norms = np.linalg.norm(vectors, axis=1)
        assert np.allclose(norms, 1.0, atol=1e-9)


def test_hashing_embedder_is_deterministic_and_stateless():
    embedder = HashingEmbedder(64)
    a = embedder.encode(["ECB raises rates"])
    b = embedder.encode(["ECB raises rates"])
    assert np.allclose(a, b)
    # Similar text is closer than unrelated text.
    same = float(embedder.encode(["ECB raises rates today"])[0] @ a[0])
    other = float(embedder.encode(["monsoon forecast above normal"])[0] @ a[0])
    assert same > other


# ---------------------------------------------------------------- novelty

def _vec(values) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    return arr / np.linalg.norm(arr, axis=1, keepdims=True)


def test_repeated_story_scores_low_novelty():
    base = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
    # Four identical stories, then one unrelated -> the unrelated one is the novel one.
    embeddings = _vec([[1, 0], [1, 0], [1, 0], [1, 0], [0, 1]])
    ids = ["a", "b", "c", "d", "e"]
    times = [base + timedelta(hours=i) for i in range(5)]
    scores = {s.event_id: s for s in compute_novelty(ids, times, embeddings, min_context=3)}

    assert scores["d"].novelty is not None
    assert scores["d"].novelty < 0.01          # already reported
    assert scores["e"].novelty > 0.9           # genuinely new
    assert scores["e"].max_similarity < 0.01


def test_novelty_is_leak_safe_ignores_future_and_ties():
    base = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
    # Event 'first' happens BEFORE its duplicates; it must not see them.
    embeddings = _vec([[1, 0], [1, 0], [1, 0], [1, 0]])
    ids = ["first", "later1", "later2", "later3"]
    times = [base, base + timedelta(hours=1), base + timedelta(hours=2), base + timedelta(hours=3)]
    scores = {s.event_id: s for s in compute_novelty(ids, times, embeddings, min_context=1)}

    # No prior context -> undefined novelty, not a fabricated value.
    assert scores["first"].novelty is None
    assert scores["first"].context_size == 0
    # A later duplicate does see priors.
    assert scores["later3"].context_size == 3


def test_same_timestamp_events_excluded_from_each_others_context():
    """Quantized timestamps make same-bucket ordering unknowable, so ties must not count."""
    base = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
    embeddings = _vec([[1, 0], [1, 0], [1, 0]])
    scores = {s.event_id: s for s in compute_novelty(
        ["x", "y", "z"], [base, base, base], embeddings, min_context=1
    )}
    assert all(s.context_size == 0 for s in scores.values())


def test_novelty_respects_lookback_window():
    base = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
    embeddings = _vec([[1, 0], [1, 0], [1, 0], [1, 0]])
    ids = ["old1", "old2", "old3", "new"]
    # First three are 30 days before the fourth -> outside a 7-day lookback.
    times = [base, base + timedelta(minutes=1), base + timedelta(minutes=2), base + timedelta(days=30)]
    scores = {s.event_id: s for s in compute_novelty(
        ids, times, embeddings, lookback_hours=168.0, min_context=1
    )}
    assert scores["new"].context_size == 0     # stale context correctly excluded


# ---------------------------------------------------------------- dedup

def test_deduplicate_collapses_syndicated_copies():
    base = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
    embeddings = _vec([[1, 0], [1, 0.02], [1, 0.01], [0, 1]])
    ids = ["wire", "outletA", "outletB", "unrelated"]
    times = [base, base + timedelta(minutes=5), base + timedelta(minutes=9), base + timedelta(minutes=20)]
    heads = ["reuters.com", "bbc.co.uk", "cnbc.com", "livemint.com"]

    canonical = deduplicate(ids, times, ["h"] * 4, embeddings, domains=heads,
                            window_hours=24, similarity_threshold=0.85)

    assert len(canonical) == 2                       # one story + one unrelated
    story = next(c for c in canonical if c.event_id == "wire")
    assert story.coverage_count == 3                 # attention proxy
    assert story.distinct_domains == 3
    assert set(story.member_ids) == {"wire", "outletA", "outletB"}


def test_deduplicate_respects_time_window():
    base = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
    embeddings = _vec([[1, 0], [1, 0]])
    # Identical text but 3 days apart -> separate events, not one story.
    canonical = deduplicate(["a", "b"], [base, base + timedelta(days=3)], ["h", "h"],
                            embeddings, window_hours=24)
    assert len(canonical) == 2
