from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np

from .schemas import RetrievalDocument
from .text_embedder import TextEmbedder

# One analysis searches this index up to ~7 times (each evidence tier plus the RAG
# confidence pass), usually with the same query text. Memoising the query embedding turns
# those into one embedder call per analysis instead of seven.
QUERY_CACHE_SIZE = 256
# Each cached score vector holds one float per document (40 KB at 5k rows, 400 KB at
# 50k). One analysis needs only its one or two query texts, so keep this cache small.
SCORE_CACHE_SIZE = 32


class LocalEvidenceIndex:
    """In-memory retrieval index over a document corpus.

    Embeddings are held as one row-normalised matrix, so a search is a single
    matrix-vector product rather than a Python loop over every document. Metadata
    filters become boolean masks over that matrix.

    This replaces a pure-Python implementation whose cost grew linearly with the corpus
    and that re-embedded the query on every call. Results are unchanged: cosine
    similarity, ties broken by insertion order, zero-norm vectors scoring 0.
    """

    def __init__(self, embedder: TextEmbedder, *, embedding_cache_dir: Path | None = None) -> None:
        """Initializes an in-memory retrieval index.

        ``embedding_cache_dir`` persists document embeddings keyed by a hash of the
        embedder and the corpus text, so a restart does not re-embed an unchanged corpus.
        """
        self._embedder = embedder
        self._documents: list[RetrievalDocument] = []
        self._matrix: np.ndarray | None = None
        self._metadata_columns: dict[str, np.ndarray] = {}
        self._query_cache: OrderedDict[str, np.ndarray] = OrderedDict()
        # Full-corpus similarity per query. The tiers of one analysis differ only in their
        # metadata mask, so score the corpus once and index the scores, rather than copying
        # the selected matrix rows (~15 MB at 5k rows) for every tier.
        self._score_cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._query_lock = threading.Lock()
        self._embedding_cache_dir = Path(embedding_cache_dir) if embedding_cache_dir else None

    def __len__(self) -> int:
        return len(self._documents)

    def add_documents(self, documents: list[RetrievalDocument]) -> None:
        """Indexes retrieval documents with deterministic embeddings."""
        if not documents:
            return
        texts = [" ".join((doc.title, doc.text)) for doc in documents]
        rows = _normalize_rows(np.asarray(self._embed_documents(texts), dtype=np.float64))
        self._matrix = rows if self._matrix is None else np.vstack([self._matrix, rows])
        self._documents.extend(documents)
        self._metadata_columns.clear()  # rebuilt lazily for the enlarged corpus
        with self._query_lock:
            self._score_cache.clear()  # scores were computed against the old corpus

    def search(
        self,
        query_text: str,
        metadata_filters: dict[str, str] | None = None,
        limit: int = 5,
    ) -> list[tuple[RetrievalDocument, float]]:
        """Searches indexed documents by semantic similarity and metadata."""
        if limit <= 0:
            raise ValueError("limit must be positive.")
        if self._matrix is None or not self._documents:
            return []

        candidates = self._candidate_rows(metadata_filters)
        if candidates.size == 0:
            return []

        scores = self._corpus_scores(query_text)[candidates]
        # Stable sort on the negated scores reproduces the previous behaviour exactly:
        # highest similarity first, equal scores kept in insertion order.
        order = np.argsort(-scores, kind="stable")[:limit]
        return [(self._documents[int(candidates[i])], float(scores[i])) for i in order]

    def _corpus_scores(self, query_text: str) -> np.ndarray:
        with self._query_lock:
            cached = self._score_cache.get(query_text)
            if cached is not None:
                self._score_cache.move_to_end(query_text)
                return cached
        assert self._matrix is not None
        scores = self._matrix @ self._query_vector(query_text)
        with self._query_lock:
            self._score_cache[query_text] = scores
            if len(self._score_cache) > SCORE_CACHE_SIZE:
                self._score_cache.popitem(last=False)
        return scores

    def _candidate_rows(self, metadata_filters: dict[str, str] | None) -> np.ndarray:
        if not metadata_filters:
            return np.arange(len(self._documents))
        mask = np.ones(len(self._documents), dtype=bool)
        for key, value in metadata_filters.items():
            mask &= self._metadata_column(key) == value
        return np.flatnonzero(mask)

    def _metadata_column(self, key: str) -> np.ndarray:
        column = self._metadata_columns.get(key)
        if column is None:
            column = np.array([doc.metadata.get(key) for doc in self._documents], dtype=object)
            self._metadata_columns[key] = column
        return column

    def _query_vector(self, query_text: str) -> np.ndarray:
        with self._query_lock:
            cached = self._query_cache.get(query_text)
            if cached is not None:
                self._query_cache.move_to_end(query_text)
                return cached
        vector = _normalize_rows(np.asarray([self._embedder.embed(query_text)], dtype=np.float64))[0]
        with self._query_lock:
            self._query_cache[query_text] = vector
            if len(self._query_cache) > QUERY_CACHE_SIZE:
                self._query_cache.popitem(last=False)
        return vector

    def _embed_documents(self, texts: list[str]) -> list[list[float]] | np.ndarray:
        if self._embedding_cache_dir is None:
            return self._embedder.embed_batch(texts)

        cache_path = self._embedding_cache_dir / f"{_corpus_key(self._embedder, texts)}.npy"
        if cache_path.exists():
            try:
                cached = np.load(cache_path)
            except (OSError, ValueError):
                cached = None
            if cached is not None and cached.ndim == 2 and cached.shape[0] == len(texts):
                return cached

        embeddings = np.asarray(self._embedder.embed_batch(texts), dtype=np.float64)
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename so an interrupted save never leaves a truncated cache file
            # that a later startup would trust.
            temp_path = cache_path.with_name(f"{cache_path.stem}.{os.getpid()}.tmp.npy")
            np.save(temp_path, embeddings)
            os.replace(temp_path, cache_path)
        except OSError:
            pass  # a cache that cannot be written only costs speed, never correctness
        return embeddings


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    """L2-normalise each row; zero-norm rows stay zero, so they score 0 as before."""
    if matrix.size == 0:
        return matrix
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    safe = np.where(norms == 0.0, 1.0, norms)
    return np.where(norms == 0.0, 0.0, matrix / safe)


def _corpus_key(embedder: TextEmbedder, texts: list[str]) -> str:
    """Identify an embedded corpus: the same embedder model over the same texts, in order."""
    identity = getattr(embedder, "cache_key", None) or f"{type(embedder).__name__}:{embedder.dimensions}"
    digest = hashlib.sha256(identity.encode("utf-8"))
    for text in texts:
        digest.update(b"\x1f")
        digest.update(text.encode("utf-8"))
    return digest.hexdigest()[:32]
