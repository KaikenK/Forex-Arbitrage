"""Phase-1 event-study core: leak-safe time alignment + per-category impact atlas.

Deliberately dependency-light (stdlib + scipy for significance) so the causal
characterization is reproducible and unit-testable without network or heavy ML.
"""
from .schema import AlignedObservation, Event, PriceSeries
from .alignment import align_event, align_events
from .atlas import CategoryImpact, build_atlas
from .reactions import ReactionFeatures, compute_reaction_features
from .stats import Correction, benjamini_hochberg, correct_atlas

__all__ = [
    "Event",
    "PriceSeries",
    "AlignedObservation",
    "align_event",
    "align_events",
    "CategoryImpact",
    "build_atlas",
    "ReactionFeatures",
    "compute_reaction_features",
    "Correction",
    "benjamini_hochberg",
    "correct_atlas",
]
