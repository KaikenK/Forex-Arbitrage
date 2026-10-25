"""Pluggable text embedders.

Three backends, same interface, so results can be compared as an ablation:
  - TfidfSvdEmbedder      : deterministic, no model download, fast. Default + test baseline.
  - HashingEmbedder       : stateless (no fit), useful for streaming/online scoring.
  - SentenceTransformerEmbedder : real contextual embeddings (all-MiniLM-L6-v2).

All return L2-normalized vectors so cosine similarity is a plain dot product.
"""
from __future__ import annotations

import hashlib
from typing import Protocol, Sequence

import numpy as np


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class Embedder(Protocol):
    name: str

    def fit(self, texts: Sequence[str]) -> "Embedder":
        ...

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        ...


class TfidfSvdEmbedder:
    """TF-IDF followed by truncated SVD (latent semantic analysis)."""

    def __init__(self, dimensions: int = 128, *, random_state: int = 0, min_df: int = 1) -> None:
        self.name = "tfidf_svd"
        self.dimensions = dimensions
        self.random_state = random_state
        self.min_df = min_df
        self._vectorizer = None
        self._svd = None

    def fit(self, texts: Sequence[str]) -> "TfidfSvdEmbedder":
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._vectorizer = TfidfVectorizer(
            lowercase=True, stop_words="english", min_df=self.min_df, ngram_range=(1, 2)
        )
        matrix = self._vectorizer.fit_transform(list(texts))
        # SVD cannot produce more components than min(n_samples, n_features) - 1.
        components = max(1, min(self.dimensions, min(matrix.shape) - 1))
        self._svd = TruncatedSVD(n_components=components, random_state=self.random_state)
        self._svd.fit(matrix)
        return self

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if self._vectorizer is None or self._svd is None:
            raise RuntimeError("TfidfSvdEmbedder.fit must be called before encode")
        matrix = self._vectorizer.transform(list(texts))
        return _l2_normalize(self._svd.transform(matrix).astype(np.float64))


class HashingEmbedder:
    """Stateless hashed bag-of-words. No fit needed, so it never leaks future vocabulary."""

    def __init__(self, dimensions: int = 256) -> None:
        self.name = "hashing"
        self.dimensions = dimensions

    def fit(self, texts: Sequence[str]) -> "HashingEmbedder":
        return self

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        vectors = np.zeros((len(texts), self.dimensions), dtype=np.float64)
        for row, text in enumerate(texts):
            for token in str(text).casefold().split():
                digest = hashlib.md5(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "little") % self.dimensions
                vectors[row, index] += 1.0
        return _l2_normalize(vectors)


class SentenceTransformerEmbedder:
    """Contextual sentence embeddings (downloads the model on first use)."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", *, batch_size: int = 64) -> None:
        self.name = f"st:{model_name}"
        self.model_name = model_name
        self.batch_size = batch_size
        self._model = None

    def fit(self, texts: Sequence[str]) -> "SentenceTransformerEmbedder":
        self._load()
        return self

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        model = self._load()
        vectors = model.encode(
            list(texts), batch_size=self.batch_size, show_progress_bar=False,
            convert_to_numpy=True, normalize_embeddings=True,
        )
        return np.asarray(vectors, dtype=np.float64)
