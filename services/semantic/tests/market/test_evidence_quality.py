"""Regression tests for the evidence term in the confidence blend.

The term used to be ``coverage_factor = min(1, len(evidence)/3)``. The retriever
always returns 5 rows, so it saturated at 1.0 on 90.7% of a 1,308-analysis archive
(stdev 0.011) and contributed a flat +0.20 to every confidence score while ignoring
how relevant the retrieved rows actually were. Confidence collapsed into a rescaled
FinBERT confidence spanning ~35 points, with half of all rows in a single decile.

It now reflects relevance, stance agreement and coverage. These tests pin the
properties that made it informative, so a future change cannot quietly flatten it.
"""
from __future__ import annotations

from collections import Counter
from statistics import pstdev

import pytest

from market.analyzer import (
    EVIDENCE_FLOOR,
    EVIDENCE_RELEVANCE_REFERENCE,
    _evidence_quality_factor,
)
from retrieval.models import RetrievedEvidence


def _evidence(relevance: float, stance: str | None = None, evidence_id: str = "ev") -> RetrievedEvidence:
    return RetrievedEvidence(
        evidence_id=evidence_id,
        source="RSS",
        evidence_type=None,
        title="title",
        snippet="snippet",
        url=None,
        published_at=None,
        relevance_score=relevance,
        stance=stance,
    )


def test_no_evidence_scores_zero():
    assert _evidence_quality_factor([]) == 0.0


def test_factor_stays_within_floor_and_one():
    """Weak evidence damps confidence but must never zero it out.

    The calibrated market model is still the dominant term at 50%; evidence is a
    modifier, not a veto.
    """
    weak = [_evidence(0.01) for _ in range(5)]
    strong = [_evidence(1.0, "supportive") for _ in range(5)]
    assert EVIDENCE_FLOOR <= _evidence_quality_factor(weak) <= 1.0
    assert EVIDENCE_FLOOR <= _evidence_quality_factor(strong) <= 1.0


def test_relevance_moves_the_factor():
    """The specific failure of the old term: relevance was ignored entirely."""
    low = [_evidence(0.05, "supportive") for _ in range(5)]
    high = [_evidence(EVIDENCE_RELEVANCE_REFERENCE, "supportive") for _ in range(5)]
    assert _evidence_quality_factor(high) > _evidence_quality_factor(low)


def test_supportive_beats_neutral_beats_contradictory():
    supportive = [_evidence(0.5, "supportive") for _ in range(5)]
    neutral = [_evidence(0.5, None) for _ in range(5)]
    contradictory = [_evidence(0.5, "contradictory") for _ in range(5)]
    assert (
        _evidence_quality_factor(supportive)
        > _evidence_quality_factor(neutral)
        > _evidence_quality_factor(contradictory)
    )


def test_thin_retrieval_scores_below_full_retrieval():
    """Two matching rows are weaker support than five equally matching rows."""
    thin = [_evidence(0.5, "supportive") for _ in range(2)]
    full = [_evidence(0.5, "supportive") for _ in range(5)]
    assert _evidence_quality_factor(thin) < _evidence_quality_factor(full)


def test_relevance_weighted_contradiction_outweighs_weak_support():
    """A strong contradiction should count for more than a weak agreement."""
    mixed = [
        _evidence(0.05, "supportive", "weak-support"),
        _evidence(0.55, "contradictory", "strong-contradiction"),
    ]
    balanced = [
        _evidence(0.55, "supportive", "strong-support"),
        _evidence(0.05, "contradictory", "weak-contradiction"),
    ]
    assert _evidence_quality_factor(mixed) < _evidence_quality_factor(balanced)


def test_uniformly_contradictory_evidence_sits_at_the_floor():
    """Fully contradicted predictions bottom out, regardless of relevance.

    Agreement is -1 there, so quality is 0 and the factor is exactly the floor.
    Pinned deliberately: it is the one input region where the term is constant,
    and test_factor_is_not_degenerate_across_realistic_inputs excludes it for
    that reason rather than because the collapse went unnoticed.
    """
    for relevance in (0.05, 0.3, 0.6):
        rows = [_evidence(relevance, "contradictory") for _ in range(5)]
        assert _evidence_quality_factor(rows) == pytest.approx(EVIDENCE_FLOOR)


def test_factor_is_not_degenerate_across_realistic_inputs():
    """The property that actually broke: no single value may dominate.

    The old term returned 1.0 for 90.7% of a 1,308-analysis archive, with a
    standard deviation of 0.011 -- effectively a constant added to every score.

    Deliberately NOT asserted: that every input combination maps to a distinct
    output. The factor is a product of relevance, agreement and coverage, so it
    has level sets -- (relevance 0.2, supportive, 3 rows) genuinely equals
    (relevance 0.4, neutral, 3 rows). Monotonic response to each input is covered
    by the tests above; what matters here is spread. Uniformly-contradictory
    inputs are excluded since they legitimately pin to the floor.
    """
    samples = []
    for relevance in [0.02, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6]:
        for stance in (None, "supportive"):
            for count in (2, 3, 5):
                samples.append(
                    _evidence_quality_factor([_evidence(relevance, stance) for _ in range(count)])
                )

    counts = Counter(round(value, 4) for value in samples)
    modal_share = counts.most_common(1)[0][1] / len(samples)
    assert modal_share < 0.25, f"one value covers {modal_share:.0%} of samples; term is collapsing"
    assert max(samples) - min(samples) > 0.25, "spread too narrow to inform confidence"
    assert pstdev(samples) > 0.05, "variance too low to move the 20% confidence weight"


@pytest.mark.parametrize("count", [1, 2, 3, 5, 8])
def test_factor_is_monotonic_in_coverage(count: int):
    """More corroborating rows never reduce the score."""
    fewer = [_evidence(0.4, "supportive") for _ in range(count)]
    more = [_evidence(0.4, "supportive") for _ in range(count + 1)]
    assert _evidence_quality_factor(more) >= _evidence_quality_factor(fewer)
