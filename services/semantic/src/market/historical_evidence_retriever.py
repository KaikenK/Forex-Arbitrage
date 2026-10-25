from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from retrieval.embedder import LocalHashingEmbedder
from retrieval.index import LocalEvidenceIndex
from retrieval.interfaces import EvidenceRetriever
from retrieval.models import EvidenceType
from retrieval.models import RetrievedEvidence
from retrieval.schemas import RetrievalDocument
from retrieval.text_embedder import TextEmbedder

from .models import MarketReactionSignal
from .schemas import AnalysisContext


class HistoricalTableEvidenceRetriever(EvidenceRetriever):
    def __init__(
        self,
        index: LocalEvidenceIndex,
        documents_by_id: dict[str, RetrievalDocument],
    ) -> None:
        """Initializes evidence retrieval from a local historical table."""
        self._index = index
        self._documents_by_id = documents_by_id

    @classmethod
    def from_jsonl(
        cls,
        training_table_path: Path,
        embedding_dimensions: int = 256,
        embedder: TextEmbedder | None = None,
    ) -> HistoricalTableEvidenceRetriever:
        """Builds a historical evidence retriever from a JSONL training table."""
        resolved_embedder = embedder or LocalHashingEmbedder(dimensions=embedding_dimensions)
        index = LocalEvidenceIndex(resolved_embedder)
        documents = _load_documents(training_table_path)
        index.add_documents(documents)
        return cls(index=index, documents_by_id={document.document_id: document for document in documents})

    async def retrieve(
        self,
        context: AnalysisContext,
        signals: list[MarketReactionSignal],
    ) -> list[RetrievedEvidence]:
        """Retrieves historical evidence rows relevant to the current article."""
        query_text = " ".join(filter(None, [context.event.headline, context.event.body or ""]))
        horizon_minutes = signals[0].horizon_minutes if signals else None

        seen_document_ids: set[str] = set()
        seen_titles: set[str] = set()
        unique_evidence: list[RetrievedEvidence] = []
        signals_by_pair = {signal.symbol: signal for signal in signals}
        for metadata_filters, matching_reason in _metadata_filter_tiers(context, horizon_minutes):
            ranked_documents = self._index.search(query_text=query_text, metadata_filters=metadata_filters, limit=15)
            for document, similarity in ranked_documents:
                if document.document_id in seen_document_ids:
                    continue
                dedup_key = f"{document.metadata.get('pair', '')}:{document.title.strip().casefold()}"
                if dedup_key in seen_titles:
                    continue
                seen_document_ids.add(document.document_id)
                seen_titles.add(dedup_key)
                signal = signals_by_pair.get(str(document.metadata.get("pair", "")))
                if signal is None and signals:
                    signal = signals[0]
                unique_evidence.append(_to_retrieved_evidence(document, similarity, matching_reason, signal))
                if len(unique_evidence) >= 5:
                    return unique_evidence
        return unique_evidence


def _load_documents(training_table_path: Path) -> list[RetrievalDocument]:
    documents: list[RetrievalDocument] = []
    for line in training_table_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw_row = json.loads(line)
        if not isinstance(raw_row, dict):
            raise ValueError("Each training table row must decode to a dictionary.")

        article_text = _required_string(raw_row, "article_text")
        timestamp = datetime.fromisoformat(_required_string(raw_row, "timestamp"))
        pair = _required_string(raw_row, "pair")
        source_metadata = raw_row.get("source_metadata", {})
        if not isinstance(source_metadata, dict):
            raise ValueError("source_metadata must be a dictionary.")

        normalized_metadata = {str(key): str(value) for key, value in source_metadata.items()}
        title, text = _split_article_text(article_text)
        document_id = str(normalized_metadata.get("event_id", f"{pair}:{timestamp.isoformat()}"))
        metadata = {
            "pair": pair,
            "source": normalized_metadata.get("source", "training_table"),
            "horizon_minutes": str(raw_row.get("horizon_minutes", "")),
            "reaction_label": _optional_value_string(raw_row.get("reaction_label"), normalized_metadata.get("reaction_label")),
            "event_category": _optional_string(raw_row, "event_category") or normalized_metadata.get("event_category", ""),
            "macro_event_type": _optional_string(raw_row, "macro_event_type") or normalized_metadata.get("macro_event_type", ""),
            "scheduled": _optional_bool_string(raw_row.get("scheduled"), normalized_metadata.get("scheduled")),
            "actual": _optional_value_string(raw_row.get("actual"), normalized_metadata.get("actual")),
            "forecast": _optional_value_string(raw_row.get("forecast"), normalized_metadata.get("forecast")),
            "previous": _optional_value_string(raw_row.get("previous"), normalized_metadata.get("previous")),
            "standardized_surprise": _optional_value_string(
                raw_row.get("standardized_surprise"),
                normalized_metadata.get("standardized_surprise"),
            ),
            "calendar_source": _optional_string(raw_row, "calendar_source") or normalized_metadata.get("calendar_source", ""),
            "source_url": _optional_string(raw_row, "source_url") or normalized_metadata.get("source_url", ""),
        }
        documents.append(
            RetrievalDocument(
                document_id=document_id,
                source=metadata["source"] or "training_table",
                title=title,
                text=text,
                timestamp=timestamp,
                metadata=metadata,
            ),
        )
    return documents


def _metadata_filter_tiers(
    context: AnalysisContext,
    horizon_minutes: int | None,
) -> list[tuple[dict[str, str] | None, str]]:
    pair = context.event.symbols[0] if context.event.symbols else None
    event_family_key, event_family_value = _event_family_filter(context)
    scheduled_value = None
    if context.article.scheduled is not None:
        scheduled_value = "true" if context.article.scheduled else "false"

    tiers: list[tuple[dict[str, str] | None, str]] = []
    if pair is not None and horizon_minutes is not None and event_family_key is not None and scheduled_value is not None:
        tiers.append(
            (
                {
                    "pair": pair,
                    "horizon_minutes": str(horizon_minutes),
                    event_family_key: event_family_value,
                    "scheduled": scheduled_value,
                },
                "pair+horizon+event_family+scheduled",
            ),
        )
    if pair is not None and horizon_minutes is not None and event_family_key is not None:
        tiers.append(
            (
                {
                    "pair": pair,
                    "horizon_minutes": str(horizon_minutes),
                    event_family_key: event_family_value,
                },
                "pair+horizon+event_family",
            ),
        )
    if pair is not None and horizon_minutes is not None and scheduled_value is not None:
        tiers.append(
            (
                {
                    "pair": pair,
                    "horizon_minutes": str(horizon_minutes),
                    "scheduled": scheduled_value,
                },
                "pair+horizon+scheduled",
            ),
        )
    if pair is not None and horizon_minutes is not None:
        tiers.append(({"pair": pair, "horizon_minutes": str(horizon_minutes)}, "pair+horizon"))
    if pair is not None and event_family_key is not None and scheduled_value is not None:
        tiers.append(
            (
                {
                    "pair": pair,
                    event_family_key: event_family_value,
                    "scheduled": scheduled_value,
                },
                "pair+event_family+scheduled",
            ),
        )
    if pair is not None and event_family_key is not None:
        tiers.append(({"pair": pair, event_family_key: event_family_value}, "pair+event_family"))
    if pair is not None:
        tiers.append(({"pair": pair}, "pair"))
    if horizon_minutes is not None:
        tiers.append(({"horizon_minutes": str(horizon_minutes)}, "horizon"))
    tiers.append((None, "semantic-similarity-only"))

    unique_tiers: list[tuple[dict[str, str] | None, str]] = []
    seen_keys: set[tuple[tuple[str, str], ...] | None] = set()
    for metadata_filters, matching_reason in tiers:
        normalized_key = None if metadata_filters is None else tuple(sorted(metadata_filters.items()))
        if normalized_key in seen_keys:
            continue
        seen_keys.add(normalized_key)
        unique_tiers.append((metadata_filters, matching_reason))
    return unique_tiers


def _event_family_filter(context: AnalysisContext) -> tuple[str | None, str | None]:
    if context.article.macro_event_type is not None and context.article.macro_event_type.strip():
        return "macro_event_type", context.article.macro_event_type.strip()
    if context.article.event_category is not None and context.article.event_category.strip():
        return "event_category", context.article.event_category.strip()
    return None, None


def _to_retrieved_evidence(
    document: RetrievalDocument,
    similarity: float,
    matching_reason: str,
    signal: MarketReactionSignal | None,
) -> RetrievedEvidence:
    metadata = document.metadata
    historical_reaction = _optional_metadata_float(metadata, "reaction_label")
    historical_direction = _reaction_direction(historical_reaction)
    return RetrievedEvidence(
        evidence_id=document.document_id,
        source=document.source,
        evidence_type=_evidence_type(metadata),
        title=document.title,
        snippet=document.text[:240],
        url=_optional_metadata_string(metadata, "source_url"),
        published_at=document.timestamp,
        relevance_score=similarity,
        pair=_optional_metadata_string(metadata, "pair"),
        horizon_minutes=_optional_metadata_int(metadata, "horizon_minutes"),
        event_category=_optional_metadata_string(metadata, "event_category"),
        macro_event_type=_optional_metadata_string(metadata, "macro_event_type"),
        scheduled=_optional_metadata_bool(metadata, "scheduled"),
        actual=_optional_metadata_float(metadata, "actual"),
        forecast=_optional_metadata_float(metadata, "forecast"),
        previous=_optional_metadata_float(metadata, "previous"),
        standardized_surprise=_optional_metadata_float(metadata, "standardized_surprise"),
        calendar_source=_optional_metadata_string(metadata, "calendar_source"),
        matching_reason=matching_reason,
        stance=_evidence_stance(signal, historical_direction),
        historical_reaction=historical_reaction,
        historical_direction=historical_direction,
    )


def _split_article_text(article_text: str) -> tuple[str, str]:
    lines = [line.strip() for line in article_text.splitlines() if line.strip()]
    if not lines:
        return "Untitled historical row", article_text
    title = lines[0][:120]
    if len(lines) == 1:
        return title, article_text
    return title, "\n".join(lines[1:]).strip()


def _evidence_type(metadata: dict[str, str]) -> EvidenceType:
    event_category = metadata.get("event_category", "").casefold()
    macro_event_type = metadata.get("macro_event_type", "").casefold()
    combined_text = " ".join(metadata.values()).casefold()
    if "central-bank" in event_category or "rate-decision" in macro_event_type:
        return EvidenceType.CENTRAL_BANK
    if any(keyword in combined_text for keyword in ("rate", "cpi", "inflation", "jobs", "calendar", "nfp")):
        return EvidenceType.ECONOMIC_CALENDAR
    return EvidenceType.ARTICLE


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string.")
    return value


def _optional_string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string when provided.")
    stripped_value = value.strip()
    if not stripped_value:
        return None
    return stripped_value


def _optional_value_string(value: object, fallback: str | None = None) -> str:
    resolved_value = value if value is not None else fallback
    if resolved_value is None:
        return ""
    return str(resolved_value)


def _optional_bool_string(value: object, fallback: str | None = None) -> str:
    resolved_value = value if value is not None else fallback
    if resolved_value is None:
        return ""
    if isinstance(resolved_value, bool):
        return "true" if resolved_value else "false"
    return str(resolved_value).strip().casefold()


def _optional_metadata_string(metadata: dict[str, str], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    stripped_value = value.strip()
    if not stripped_value:
        return None
    return stripped_value


def _optional_metadata_int(metadata: dict[str, str], key: str) -> int | None:
    value = _optional_metadata_string(metadata, key)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _optional_metadata_float(metadata: dict[str, str], key: str) -> float | None:
    value = _optional_metadata_string(metadata, key)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _optional_metadata_bool(metadata: dict[str, str], key: str) -> bool | None:
    value = _optional_metadata_string(metadata, key)
    if value is None:
        return None
    normalized_value = value.casefold()
    if normalized_value in {"true", "1", "yes"}:
        return True
    if normalized_value in {"false", "0", "no"}:
        return False
    return None


def _reaction_direction(reaction_label: float | None) -> float | None:
    if reaction_label is None:
        return None
    if reaction_label > 0:
        return 1.0
    if reaction_label < 0:
        return -1.0
    return 0.0


def _evidence_stance(
    signal: MarketReactionSignal | None,
    historical_direction: float | None,
) -> str:
    if signal is None or historical_direction is None:
        return "neutral"
    if signal.direction == 0 or historical_direction == 0:
        return "neutral"
    if signal.direction * historical_direction > 0:
        return "supportive"
    return "contradictory"
