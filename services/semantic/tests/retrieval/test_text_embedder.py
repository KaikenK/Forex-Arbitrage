from __future__ import annotations

import sys
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from retrieval.embedder import LocalHashingEmbedder
from retrieval.text_embedder import TextEmbedder


def test_local_hashing_embedder_implements_text_embedder() -> None:
    embedder = LocalHashingEmbedder(dimensions=64)
    assert isinstance(embedder, TextEmbedder)


def test_local_hashing_embedder_dimensions_property() -> None:
    embedder = LocalHashingEmbedder(dimensions=128)
    assert embedder.dimensions == 128


def test_local_hashing_embedder_embed_returns_correct_length() -> None:
    embedder = LocalHashingEmbedder(dimensions=64)
    result = embedder.embed("test input")
    assert len(result) == 64


def test_local_hashing_embedder_embed_is_normalized() -> None:
    embedder = LocalHashingEmbedder(dimensions=64)
    result = embedder.embed("some text for embedding")
    norm = sum(v * v for v in result) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_local_hashing_embedder_embed_is_deterministic() -> None:
    embedder = LocalHashingEmbedder(dimensions=64)
    result1 = embedder.embed("deterministic test")
    result2 = embedder.embed("deterministic test")
    assert result1 == result2
