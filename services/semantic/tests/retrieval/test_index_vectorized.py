"""The vectorised index must rank exactly like brute-force cosine, and cost must not
scale with the number of times one analysis searches it.

Before: pure-Python cosine over every document, query re-embedded on every call.
At 5,000 documents that was ~1.2 s per article; the matrix version is ~11 ms. These tests
pin the behaviour that made that safe.
"""
from __future__ import annotations

import math
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from retrieval.embedder import LocalHashingEmbedder
from retrieval.index import LocalEvidenceIndex
from retrieval.schemas import RetrievalDocument
from retrieval.text_embedder import TextEmbedder


class CountingEmbedder(TextEmbedder):
    """Wraps the hashing embedder and records every call."""

    def __init__(self, dimensions: int = 64) -> None:
        self._inner = LocalHashingEmbedder(dimensions=dimensions)
        self.query_calls = 0
        self.batch_calls = 0

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    @property
    def cache_key(self) -> str:
        return self._inner.cache_key

    def embed(self, text: str) -> list[float]:
        self.query_calls += 1
        return self._inner.embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls += 1
        return [self._inner.embed(text) for text in texts]


def _doc(index: int, text: str, pair: str = "USD/INR", horizon: str = "60") -> RetrievalDocument:
    return RetrievalDocument(
        document_id=f"doc-{index}",
        source="RSS",
        title=text,
        text="",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        metadata={"pair": pair, "horizon_minutes": horizon},
    )


CORPUS = [
    _doc(0, "RBI holds repo rate steady"),
    _doc(1, "rupee weakens on oil prices", horizon="240"),
    _doc(2, "fed signals patience on rate cuts", pair="EUR/USD"),
    _doc(3, "RBI holds repo rate steady"),  # exact duplicate of doc-0: tests tie-breaking
    _doc(4, "payrolls beat forecast dollar strengthens", pair="EUR/USD", horizon="240"),
    _doc(5, "inflation cools as rupee steadies"),
    _doc(6, ""),  # empty text -> zero vector -> must score 0, not NaN
]


def _brute_force(query: str, filters: dict[str, str] | None, limit: int) -> list[tuple[str, float]]:
    """Reference implementation: the original pure-Python cosine, insertion-order ties."""
    embedder = LocalHashingEmbedder(dimensions=64)
    q = embedder.embed(query)
    scored = []
    for doc in CORPUS:
        if filters and any(doc.metadata.get(key) != value for key, value in filters.items()):
            continue
        v = embedder.embed(" ".join((doc.title, doc.text)))
        num = sum(a * b for a, b in zip(q, v))
        qn = math.sqrt(sum(a * a for a in q))
        vn = math.sqrt(sum(a * a for a in v))
        scored.append((doc.document_id, 0.0 if qn == 0 or vn == 0 else num / (qn * vn)))
    return sorted(scored, key=lambda item: item[1], reverse=True)[:limit]


def _index(embedder: TextEmbedder | None = None, **kwargs) -> LocalEvidenceIndex:
    index = LocalEvidenceIndex(embedder or LocalHashingEmbedder(dimensions=64), **kwargs)
    index.add_documents(CORPUS)
    return index


def test_matches_brute_force_ranking_and_scores() -> None:
    index = _index()
    cases = [
        ("RBI repo rate decision", None),
        ("RBI repo rate decision", {"pair": "USD/INR"}),
        ("rupee oil", {"pair": "USD/INR", "horizon_minutes": "240"}),
        ("dollar payrolls", {"pair": "EUR/USD"}),
        ("nothing matches this", {"pair": "GBP/USD"}),
        ("", None),
    ]
    for query, filters in cases:
        expected = _brute_force(query, filters, limit=10)
        actual = [(doc.document_id, score) for doc, score in index.search(query, filters, limit=10)]
        assert [doc_id for doc_id, _ in actual] == [doc_id for doc_id, _ in expected], (query, filters)
        for (_, got), (_, want) in zip(actual, expected):
            assert math.isclose(got, want, abs_tol=1e-12)


def test_equal_scores_keep_insertion_order() -> None:
    results = _index().search("RBI holds repo rate steady", limit=2)
    assert [doc.document_id for doc, _ in results] == ["doc-0", "doc-3"]


def test_zero_vector_scores_zero_not_nan() -> None:
    scores = {doc.document_id: score for doc, score in _index().search("rupee", limit=10)}
    assert scores["doc-6"] == 0.0


def test_repeated_searches_embed_the_query_once() -> None:
    """One analysis searches up to ~7 times with the same text; embed it once."""
    embedder = CountingEmbedder()
    index = _index(embedder)
    for filters in ({"pair": "USD/INR"}, {"pair": "USD/INR", "horizon_minutes": "60"}, None, None):
        index.search("RBI repo rate", filters, limit=5)
    assert embedder.query_calls == 1


def test_adding_documents_invalidates_cached_scores() -> None:
    index = _index()
    before = index.search("brand new topic", limit=1)
    index.add_documents([_doc(99, "brand new topic")])
    after = index.search("brand new topic", limit=1)
    assert after[0][0].document_id == "doc-99"
    assert after[0][1] > before[0][1]


def test_disk_cache_skips_re_embedding_unchanged_corpus(tmp_path: Path) -> None:
    first = CountingEmbedder()
    _index(first, embedding_cache_dir=tmp_path)
    assert first.batch_calls == 1

    second = CountingEmbedder()
    warm = _index(second, embedding_cache_dir=tmp_path)
    assert second.batch_calls == 0, "unchanged corpus must load from the cache"
    assert warm.search("RBI repo rate", limit=1)[0][0].document_id == "doc-0"


def test_disk_cache_is_keyed_on_corpus_content(tmp_path: Path) -> None:
    _index(CountingEmbedder(), embedding_cache_dir=tmp_path)
    changed = CountingEmbedder()
    index = LocalEvidenceIndex(changed, embedding_cache_dir=tmp_path)
    index.add_documents(CORPUS + [_doc(50, "different corpus")])
    assert changed.batch_calls == 1, "a changed corpus must not reuse stale embeddings"


def test_corrupt_cache_file_is_ignored(tmp_path: Path) -> None:
    _index(CountingEmbedder(), embedding_cache_dir=tmp_path)
    for path in tmp_path.glob("*.npy"):
        path.write_bytes(b"not a numpy file")
    recovering = CountingEmbedder()
    index = _index(recovering, embedding_cache_dir=tmp_path)
    assert recovering.batch_calls == 1
    assert index.search("RBI repo rate", limit=1)[0][0].document_id == "doc-0"


def test_empty_index_returns_nothing() -> None:
    assert LocalEvidenceIndex(LocalHashingEmbedder(dimensions=64)).search("anything", limit=5) == []
