from __future__ import annotations

from datetime import UTC
from datetime import datetime

from .models import ArticleAnalysis


class HistoryFilters:
    def __init__(
        self,
        *,
        pair: str | None = None,
        source: str | None = None,
        event_category: str | None = None,
        text_query: str | None = None,
        event_id: str | None = None,
        has_evidence: bool | None = None,
        min_confidence: float | None = None,
        from_timestamp: datetime | None = None,
        to_timestamp: datetime | None = None,
    ) -> None:
        self.pair = pair.strip().upper() if isinstance(pair, str) and pair.strip() else None
        self.source = source.strip().upper() if isinstance(source, str) and source.strip() else None
        self.event_category = event_category.strip().casefold() if isinstance(event_category, str) and event_category.strip() else None
        self.text_query = text_query.strip().casefold() if isinstance(text_query, str) and text_query.strip() else None
        self.event_id = event_id.strip() if isinstance(event_id, str) and event_id.strip() else None
        self.has_evidence = has_evidence
        self.min_confidence = min_confidence
        self.from_timestamp = from_timestamp.astimezone(UTC) if from_timestamp is not None else None
        self.to_timestamp = to_timestamp.astimezone(UTC) if to_timestamp is not None else None

    def matches(self, analysis: ArticleAnalysis) -> bool:
        if self.event_id is not None and analysis.event_id != self.event_id:
            return False

        if self.pair is not None:
            symbols = [symbol.upper() for symbol in analysis.affected_symbols]
            evidence_pairs = [evidence.pair.upper() for evidence in analysis.evidence if isinstance(evidence.pair, str)]
            if self.pair not in symbols and self.pair not in evidence_pairs:
                return False

        if self.source is not None:
            evidence_sources = [evidence.source.upper() for evidence in analysis.evidence]
            if self.source not in evidence_sources:
                return False

        if self.event_category is not None:
            evidence_categories = [
                evidence.event_category.casefold()
                for evidence in analysis.evidence
                if isinstance(evidence.event_category, str) and evidence.event_category.strip()
            ]
            if self.event_category not in evidence_categories:
                return False

        if self.has_evidence is not None and bool(analysis.evidence) != self.has_evidence:
            return False

        if self.min_confidence is not None and analysis.confidence < self.min_confidence:
            return False

        if self.from_timestamp is not None and analysis.produced_at < self.from_timestamp:
            return False

        if self.to_timestamp is not None and analysis.produced_at > self.to_timestamp:
            return False

        if self.text_query is not None:
            haystacks = [analysis.event_id.casefold(), analysis.signal_rationale.casefold()]
            haystacks.extend(driver.label.casefold() for driver in analysis.key_drivers)
            haystacks.extend(driver.rationale.casefold() for driver in analysis.key_drivers)
            haystacks.extend(evidence.title.casefold() for evidence in analysis.evidence)
            haystacks.extend(evidence.snippet.casefold() for evidence in analysis.evidence)
            if not any(self.text_query in haystack for haystack in haystacks):
                return False

        return True