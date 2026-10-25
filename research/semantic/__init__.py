"""Semantic layer: embeddings, novelty scoring, and syndication dedup.

Supports the N1 research claim — that *semantic novelty* (how much new information a headline
carries relative to recent news) predicts FX reaction magnitude better than sentiment polarity.
"""
from .embeddings import Embedder, HashingEmbedder, SentenceTransformerEmbedder, TfidfSvdEmbedder
from .novelty import NoveltyScore, compute_novelty
from .dedup import CanonicalEvent, deduplicate

__all__ = [
    "Embedder",
    "TfidfSvdEmbedder",
    "HashingEmbedder",
    "SentenceTransformerEmbedder",
    "NoveltyScore",
    "compute_novelty",
    "CanonicalEvent",
    "deduplicate",
]
