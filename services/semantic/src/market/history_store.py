from __future__ import annotations

from dataclasses import asdict
from dataclasses import is_dataclass
from datetime import UTC
from datetime import datetime
from enum import Enum
import json
from pathlib import Path
from typing import Any

from retrieval.models import EvidenceType
from retrieval.models import RetrievedEvidence

from .models import ArticleAnalysis
from .models import KeyDriver
from .models import RiskFlag
from .models import TradingSignal


class JsonlAnalysisHistoryStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[ArticleAnalysis]:
        if not self._path.exists():
            return []

        analyses: list[ArticleAnalysis] = []
        for raw_line in self._path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            try:
                analyses.append(article_analysis_from_payload(payload))
            except (TypeError, ValueError, KeyError):
                continue

        analyses.sort(key=lambda item: item.produced_at, reverse=True)
        return analyses

    def append(self, analysis: ArticleAnalysis) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_json_compatible(asdict(analysis)), ensure_ascii=True))
            handle.write("\n")


def article_analysis_from_payload(payload: dict[str, Any]) -> ArticleAnalysis:
    return ArticleAnalysis(
        event_id=str(payload["event_id"]),
        sentiment_score=float(payload["sentiment_score"]),
        market_score=float(payload["market_score"]),
        affected_symbols=[str(symbol) for symbol in payload.get("affected_symbols", [])],
        confidence=float(payload["confidence"]),
        evidence=[retrieved_evidence_from_payload(item) for item in payload.get("evidence", [])],
        risk_flags=[RiskFlag(str(flag)) for flag in payload.get("risk_flags", [])],
        produced_at=_parse_datetime(payload.get("produced_at")),
        key_drivers=[key_driver_from_payload(item) for item in payload.get("key_drivers", [])],
        methodology={str(key): str(value) for key, value in dict(payload.get("methodology", {})).items()},
        trading_signal=TradingSignal(str(payload.get("trading_signal", TradingSignal.HOLD.value))),
        signal_rationale=str(payload.get("signal_rationale", "")),
        # Records written before multi-pair scoring have no pair_scores; they load as empty.
        pair_scores=_pair_scores_from_payload(payload.get("pair_scores")),
    )


def _pair_scores_from_payload(value: object) -> dict[str, dict[str, float]]:
    if not isinstance(value, dict):
        return {}
    scores: dict[str, dict[str, float]] = {}
    for pair, fields in value.items():
        if not isinstance(fields, dict):
            continue
        scores[str(pair)] = {
            str(key): float(item)
            for key, item in fields.items()
            if isinstance(item, int | float) and not isinstance(item, bool)
        }
    return scores


def retrieved_evidence_from_payload(payload: dict[str, Any]) -> RetrievedEvidence:
    return RetrievedEvidence(
        evidence_id=str(payload["evidence_id"]),
        source=str(payload["source"]),
        evidence_type=EvidenceType(str(payload.get("evidence_type", EvidenceType.OTHER.value))),
        title=str(payload.get("title", "")),
        snippet=str(payload.get("snippet", "")),
        url=_optional_str(payload.get("url")),
        published_at=_optional_datetime(payload.get("published_at")),
        relevance_score=float(payload.get("relevance_score", 0.0)),
        pair=_optional_str(payload.get("pair")),
        horizon_minutes=_optional_int(payload.get("horizon_minutes")),
        event_category=_optional_str(payload.get("event_category")),
        macro_event_type=_optional_str(payload.get("macro_event_type")),
        scheduled=_optional_bool(payload.get("scheduled")),
        actual=_optional_float(payload.get("actual")),
        forecast=_optional_float(payload.get("forecast")),
        previous=_optional_float(payload.get("previous")),
        standardized_surprise=_optional_float(payload.get("standardized_surprise")),
        calendar_source=_optional_str(payload.get("calendar_source")),
        matching_reason=_optional_str(payload.get("matching_reason")),
        stance=_optional_str(payload.get("stance")),
        historical_reaction=_optional_float(payload.get("historical_reaction")),
        historical_direction=_optional_float(payload.get("historical_direction")),
    )


def key_driver_from_payload(payload: dict[str, Any]) -> KeyDriver:
    return KeyDriver(
        label=str(payload.get("label", "")),
        rationale=str(payload.get("rationale", "")),
        evidence_ids=[str(item) for item in payload.get("evidence_ids", [])],
    )


def _json_compatible(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_compatible(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    return value


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("datetime value must be a non-empty string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return _parse_datetime(value)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        return int(float(value))
    return None


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        return float(value)
    return None


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None