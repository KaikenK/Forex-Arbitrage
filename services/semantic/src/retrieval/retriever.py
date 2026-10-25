from __future__ import annotations

from .index import LocalEvidenceIndex
from .schemas import RetrievalResult


class HistoricalEvidenceRetriever:
    def __init__(self, index: LocalEvidenceIndex) -> None:
        """Initializes a local historical evidence retriever."""
        self._index = index

    def retrieve(
        self,
        query_text: str,
        metadata_filters: dict[str, str] | None = None,
        limit: int = 5,
    ) -> list[RetrievalResult]:
        """Retrieves ranked historical evidence with relevance reasons."""
        ranked_documents = self._index.search(
            query_text=query_text,
            metadata_filters=metadata_filters,
            limit=limit,
        )
        return [
            RetrievalResult(
                document_id=document.document_id,
                source=document.source,
                title=document.title,
                timestamp=document.timestamp,
                similarity=similarity,
                relevance_reason=self._relevance_reason(document.metadata),
                metadata=dict(document.metadata),
            )
            for document, similarity in ranked_documents
        ]

    def _relevance_reason(self, metadata: dict[str, str]) -> str:
        document_type = metadata.get("document_type")
        if document_type == "economic_calendar":
            return "similar economic calendar event"
        if document_type == "model_output":
            return "prior reactions for the same pair"
        return "similar article"
