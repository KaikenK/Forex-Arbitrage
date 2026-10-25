from __future__ import annotations

import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from retrieval.embedder import LocalHashingEmbedder
from retrieval.index import LocalEvidenceIndex
from retrieval.retriever import HistoricalEvidenceRetriever
from retrieval.schemas import RetrievalDocument


def make_document(
    *,
    document_id: str,
    title: str,
    text: str,
    document_type: str,
    pair: str,
) -> RetrievalDocument:
    return RetrievalDocument(
        document_id=document_id,
        source="local_corpus",
        title=title,
        text=text,
        timestamp=datetime(2026, 3, 25, 12, 0, tzinfo=UTC),
        metadata={
            "document_type": document_type,
            "pair": pair,
            "snippet": text[:80],
        },
    )


def test_retriever_ranks_semantically_similar_documents() -> None:
    embedder = LocalHashingEmbedder(dimensions=64)
    index = LocalEvidenceIndex(embedder)
    index.add_documents(
        [
            make_document(
                document_id="doc-1",
                title="EUR/USD reacts to CPI surprise",
                text="eur usd cpi inflation surprise reaction",
                document_type="article",
                pair="EUR/USD",
            ),
            make_document(
                document_id="doc-2",
                title="USD/JPY waits for BOJ",
                text="usd jpy boj central bank outlook",
                document_type="article",
                pair="USD/JPY",
            ),
        ],
    )

    results = HistoricalEvidenceRetriever(index).retrieve("eur usd inflation reaction", limit=2)

    assert [result.document_id for result in results] == ["doc-1", "doc-2"]
    assert results[0].similarity >= results[1].similarity
    assert results[0].relevance_reason == "similar article"


def test_retriever_applies_metadata_filters_and_reason_mapping() -> None:
    embedder = LocalHashingEmbedder(dimensions=64)
    index = LocalEvidenceIndex(embedder)
    index.add_documents(
        [
            make_document(
                document_id="doc-article",
                title="EUR/USD article history",
                text="eur usd article reaction",
                document_type="article",
                pair="EUR/USD",
            ),
            make_document(
                document_id="doc-calendar",
                title="US CPI event",
                text="cpi economic calendar usd inflation",
                document_type="economic_calendar",
                pair="EUR/USD",
            ),
            make_document(
                document_id="doc-model",
                title="Prior EUR/USD reaction",
                text="prior eur usd market reaction output",
                document_type="model_output",
                pair="EUR/USD",
            ),
        ],
    )

    results = HistoricalEvidenceRetriever(index).retrieve(
        "eur usd inflation reaction",
        metadata_filters={"pair": "EUR/USD"},
        limit=3,
    )

    assert {result.document_id for result in results} == {
        "doc-article",
        "doc-calendar",
        "doc-model",
    }
    reasons = {result.document_id: result.relevance_reason for result in results}
    assert reasons["doc-article"] == "similar article"
    assert reasons["doc-calendar"] == "similar economic calendar event"
    assert reasons["doc-model"] == "prior reactions for the same pair"
