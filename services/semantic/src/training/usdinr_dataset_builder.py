from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any

from news.models import SentimentSource
from news.models import SourceType
from news.models import UnifiedNewsEvent

from .labels.event_alignment import EventAlignment
from .labels.pair_mapper import PairMapper
from .labels.price_window_labeler import PriceWindowLabeler
from .models import MarketReactionTrainingRow
from .models import PriceBar

DEFAULT_PAIR = "USD/INR"
DEFAULT_SOURCE = SourceType.RSS
STANDARDIZED_SURPRISE_FORMULA = (
    "relative_to_consensus=(actual-forecast)/abs(forecast); "
    "falls back to abs(previous) or 1.0 when forecast is zero"
)
TOP_LEVEL_METADATA_FIELDS = (
    "event_category",
    "macro_event_type",
    "calendar_source",
    "source_url",
    "scheduled",
    "actual",
    "forecast",
    "previous",
    "standardized_surprise",
)


@dataclass(frozen=True)
class TimestampedEventSeed:
    event_id: str
    headline: str
    body: str | None
    published_at: datetime
    source: SourceType
    sentiment_label: float
    symbols: list[str]
    source_metadata: dict[str, str]
    event_category: str | None
    macro_event_type: str | None
    scheduled: bool
    actual: float | None
    forecast: float | None
    previous: float | None
    standardized_surprise: float | None
    calendar_source: str | None
    source_url: str | None


def load_timestamped_event_seeds(
    input_path: Path,
    *,
    default_pair: str = DEFAULT_PAIR,
) -> list[TimestampedEventSeed]:
    """Loads timestamped event seeds from a JSONL file."""
    if not input_path.exists():
        raise FileNotFoundError(
            f"Timestamped event seed file not found: {input_path}. "
            "Create a JSONL with at least 'headline' and 'published_at'."
        )

    seeds: list[TimestampedEventSeed] = []
    seen_seed_keys: set[tuple[str, str, tuple[str, ...]]] = set()
    for line_number, line in enumerate(input_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue

        raw_seed = json.loads(line)
        if not isinstance(raw_seed, dict):
            raise ValueError(f"Line {line_number} must decode to a JSON object.")

        event_id = _optional_string(raw_seed.get("event_id")) or f"usdinr-event-{line_number:05d}"
        source = _parse_source(raw_seed.get("source"), line_number=line_number)
        published_at = _parse_timestamp(_required_string(raw_seed, "published_at", line_number), line_number)
        symbols = _parse_symbols(raw_seed, default_pair=default_pair)
        source_url = _optional_string(raw_seed.get("source_url"))
        dedup_key = _seed_dedup_key(
            headline=_required_string(raw_seed, "headline", line_number),
            published_at=published_at,
            symbols=symbols,
            source_url=source_url,
        )
        if dedup_key in seen_seed_keys:
            continue
        seen_seed_keys.add(dedup_key)

        actual = _optional_float_value(
            raw_seed.get("actual"),
            field_name="actual",
            line_number=line_number,
        )
        forecast = _optional_float_value(
            raw_seed.get("forecast"),
            field_name="forecast",
            line_number=line_number,
        )
        previous = _optional_float_value(
            raw_seed.get("previous"),
            field_name="previous",
            line_number=line_number,
        )
        standardized_surprise = _resolved_standardized_surprise(
            explicit_value=raw_seed.get("standardized_surprise"),
            actual=actual,
            forecast=forecast,
            previous=previous,
            line_number=line_number,
        )
        source_metadata = _merged_source_metadata(raw_seed, event_id=event_id, source=source)
        if standardized_surprise is not None and "standardized_surprise" not in source_metadata:
            source_metadata["standardized_surprise"] = _metadata_value(standardized_surprise)
        if actual is not None and forecast is not None and "surprise_formula" not in source_metadata:
            source_metadata["surprise_formula"] = STANDARDIZED_SURPRISE_FORMULA

        seeds.append(
            TimestampedEventSeed(
                event_id=event_id,
                headline=_required_string(raw_seed, "headline", line_number),
                body=_optional_string(raw_seed.get("body")),
                published_at=published_at,
                source=source,
                sentiment_label=_float_value(
                    raw_seed.get("sentiment_label", raw_seed.get("sentiment", 0.0)),
                    field_name="sentiment_label",
                    line_number=line_number,
                ),
                symbols=symbols,
                source_metadata=source_metadata,
                event_category=_optional_string(raw_seed.get("event_category")),
                macro_event_type=_optional_string(raw_seed.get("macro_event_type")),
                scheduled=_bool_value(
                    raw_seed.get("scheduled", False),
                    field_name="scheduled",
                    line_number=line_number,
                ),
                actual=actual,
                forecast=forecast,
                previous=previous,
                standardized_surprise=standardized_surprise,
                calendar_source=_optional_string(raw_seed.get("calendar_source")),
                source_url=source_url,
            ),
        )

    if not seeds:
        raise ValueError(f"No timestamped event seeds were loaded from {input_path}.")
    return seeds


def build_training_rows_from_events(
    event_seeds: list[TimestampedEventSeed],
    price_history: dict[str, list[PriceBar]],
    horizons_minutes: list[int],
    *,
    candidate_symbols: list[str] | None = None,
) -> list[MarketReactionTrainingRow]:
    """Builds labeled USD/INR rows from timestamped events and aligned price bars."""
    labeler = PriceWindowLabeler(EventAlignment(), PairMapper())
    fallback_symbols = candidate_symbols or [DEFAULT_PAIR]
    rows: list[MarketReactionTrainingRow] = []

    for event_seed in event_seeds:
        rows.extend(
            labeler.build_training_rows(
                event=_to_unified_news_event(event_seed),
                candidate_symbols=fallback_symbols,
                price_history=price_history,
                horizons_minutes=horizons_minutes,
                source_metadata=event_seed.source_metadata,
                row_overrides={
                    "event_category": event_seed.event_category,
                    "macro_event_type": event_seed.macro_event_type,
                    "scheduled": event_seed.scheduled,
                    "actual": event_seed.actual,
                    "forecast": event_seed.forecast,
                    "previous": event_seed.previous,
                    "standardized_surprise": event_seed.standardized_surprise,
                    "calendar_source": event_seed.calendar_source,
                    "source_url": event_seed.source_url,
                },
            ),
        )

    return rows


def serialize_training_rows(rows: list[MarketReactionTrainingRow]) -> list[dict[str, Any]]:
    """Converts dataclass rows to JSON-serializable dictionaries."""
    return [
        {
            "article_text": row.article_text,
            "timestamp": row.timestamp.isoformat(),
            "pair": row.pair,
            "horizon_minutes": row.horizon_minutes,
            "sentiment_label": row.sentiment_label,
            "reaction_label": row.reaction_label,
            "magnitude_label": row.magnitude_label,
            "source_metadata": dict(row.source_metadata),
            "event_category": row.event_category,
            "macro_event_type": row.macro_event_type,
            "scheduled": row.scheduled,
            "actual": row.actual,
            "forecast": row.forecast,
            "previous": row.previous,
            "standardized_surprise": row.standardized_surprise,
            "calendar_source": row.calendar_source,
            "source_url": row.source_url,
        }
        for row in rows
    ]


def _to_unified_news_event(event_seed: TimestampedEventSeed) -> UnifiedNewsEvent:
    return UnifiedNewsEvent(
        event_id=event_seed.event_id,
        schema_version=1,
        source=event_seed.source,
        headline=event_seed.headline,
        body=event_seed.body,
        published_at=event_seed.published_at,
        symbols=list(event_seed.symbols),
        raw_sentiment=event_seed.sentiment_label,
        sentiment_source=SentimentSource.NONE,
        ingested_at=event_seed.published_at,
    )


def _required_string(raw_seed: dict[str, Any], field_name: str, line_number: int) -> str:
    value = _optional_string(raw_seed.get(field_name))
    if value is None:
        raise ValueError(f"Line {line_number} is missing required field '{field_name}'.")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Expected a string value, got {type(value).__name__}.")
    stripped_value = value.strip()
    if not stripped_value:
        return None
    return stripped_value


def _float_value(value: object, *, field_name: str, line_number: int) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Line {line_number} field '{field_name}' must be numeric.")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError as exc:
            raise ValueError(f"Line {line_number} field '{field_name}' must be numeric.") from exc
    raise ValueError(f"Line {line_number} field '{field_name}' must be numeric.")


def _optional_float_value(value: object, *, field_name: str, line_number: int) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _float_value(value, field_name=field_name, line_number=line_number)


def _resolved_standardized_surprise(
    *,
    explicit_value: object,
    actual: float | None,
    forecast: float | None,
    previous: float | None,
    line_number: int,
) -> float | None:
    explicit_surprise = _optional_float_value(
        explicit_value,
        field_name="standardized_surprise",
        line_number=line_number,
    )
    if explicit_surprise is not None:
        return explicit_surprise
    if actual is None or forecast is None:
        return None

    scale = abs(forecast)
    if scale < 1e-12 and previous is not None:
        scale = abs(previous)
    if scale < 1e-12:
        scale = 1.0
    return (actual - forecast) / scale


def _bool_value(value: object, *, field_name: str, line_number: int) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized_value = value.strip().casefold()
        if normalized_value in {"true", "1", "yes"}:
            return True
        if normalized_value in {"false", "0", "no"}:
            return False
    raise ValueError(f"Line {line_number} field '{field_name}' must be boolean.")


def _parse_timestamp(value: str, line_number: int) -> datetime:
    try:
        parsed_timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Line {line_number} field 'published_at' must be ISO-8601.") from exc

    if parsed_timestamp.tzinfo is None:
        return parsed_timestamp.replace(tzinfo=UTC)
    return parsed_timestamp.astimezone(UTC)


def _parse_source(value: object, *, line_number: int) -> SourceType:
    if value is None:
        return DEFAULT_SOURCE
    normalized_source = _optional_string(value)
    if normalized_source is None:
        return DEFAULT_SOURCE
    try:
        return SourceType(normalized_source.replace("-", "_").upper())
    except ValueError as exc:
        valid_sources = ", ".join(source.value for source in SourceType)
        raise ValueError(
            f"Line {line_number} field 'source' must be one of: {valid_sources}."
        ) from exc


def _parse_symbols(raw_seed: dict[str, Any], *, default_pair: str) -> list[str]:
    candidate_values: list[str] = []
    raw_symbols = raw_seed.get("symbols")
    if isinstance(raw_symbols, list):
        for value in raw_symbols:
            symbol = _optional_string(value)
            if symbol is not None:
                candidate_values.append(symbol)
    elif raw_symbols is not None:
        raise ValueError("Field 'symbols' must be a list of strings when present.")

    for field_name in ("pair", "currency_pair"):
        symbol = _optional_string(raw_seed.get(field_name))
        if symbol is not None:
            candidate_values.append(symbol)

    if not candidate_values:
        return [default_pair]

    ordered_symbols: list[str] = []
    for symbol in candidate_values:
        if symbol not in ordered_symbols:
            ordered_symbols.append(symbol)
    return ordered_symbols


def _merged_source_metadata(
    raw_seed: dict[str, Any],
    *,
    event_id: str,
    source: SourceType,
) -> dict[str, str]:
    metadata = _coerce_metadata(raw_seed.get("source_metadata"))
    for field_name in TOP_LEVEL_METADATA_FIELDS:
        value = raw_seed.get(field_name)
        if value is None:
            continue
        metadata[field_name] = _metadata_value(value)
    metadata["event_id"] = event_id
    metadata["source"] = source.value
    return metadata


def _coerce_metadata(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Field 'source_metadata' must be a JSON object when present.")
    return {
        str(key): _metadata_value(item)
        for key, item in value.items()
        if item is not None and _metadata_value(item)
    }


def _metadata_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _seed_dedup_key(
    *,
    headline: str,
    published_at: datetime,
    symbols: list[str],
    source_url: str | None,
) -> tuple[str, str, tuple[str, ...]]:
    if source_url is not None:
        identity = source_url.casefold()
    else:
        identity = _normalized_headline(headline)
    return (identity, published_at.isoformat(), tuple(symbols))


def _normalized_headline(headline: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", headline.casefold()).strip()