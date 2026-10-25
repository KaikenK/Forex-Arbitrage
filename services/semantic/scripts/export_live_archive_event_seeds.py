from __future__ import annotations

import argparse
import json
import site
import sys
from collections import Counter
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
USER_SITE_PATH = Path(site.getusersitepackages())
for import_path in (str(USER_SITE_PATH), str(SRC_PATH)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from market.pair_assets import pair_slug
from market.pair_assets import runtime_archive_dir
from market.pair_assets import sentiment_assets_dir
from news.models import SourceType

DEFAULT_PAIR = "USD/INR"


def main() -> None:
    args = _parse_args()
    analysis_records = _read_jsonl(args.analysis_archive.resolve())
    # Oldest-first: _seed_identity keys on headline alone, so whichever record is seen
    # first is the one kept. That must be the earliest sighting, because its timestamp
    # becomes the anchor for the forward-return label.
    analysis_records.sort(key=_analysis_sort_key)
    feed_records = _read_jsonl(args.feed_archive.resolve()) if args.feed_archive.exists() else []
    existing_seed_keys, existing_event_ids = _load_existing_seed_identity(args.output_path.resolve())
    feed_index = _build_feed_index(feed_records)

    counters: Counter[str] = Counter()
    new_seed_records: list[dict[str, Any]] = []
    emitted_seed_keys: set[tuple[str, str]] = set()
    emitted_event_ids: set[str] = set()

    for analysis_record in analysis_records:
        counters["analysis_records"] += 1
        seed_record = _analysis_to_seed_record(
            analysis_record,
            feed_index=feed_index,
            pair=args.pair,
        )
        if seed_record is None:
            counters["skipped_ineligible"] += 1
            continue

        seed_key = _seed_identity(seed_record)
        event_id = str(seed_record["event_id"])
        if event_id in existing_event_ids or event_id in emitted_event_ids:
            counters["skipped_existing_event_id"] += 1
            continue
        if seed_key in existing_seed_keys or seed_key in emitted_seed_keys:
            counters["skipped_existing_seed"] += 1
            continue

        emitted_event_ids.add(event_id)
        emitted_seed_keys.add(seed_key)
        new_seed_records.append(seed_record)
        counters["seed_records"] += 1

    if args.limit is not None:
        new_seed_records = new_seed_records[: args.limit]

    if not args.dry_run and new_seed_records:
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        with args.output_path.open("a", encoding="utf-8") as handle:
            for seed_record in new_seed_records:
                handle.write(json.dumps(seed_record, ensure_ascii=True))
                handle.write("\n")

    print(f"Analysis archive: {args.analysis_archive.resolve()}")
    print(f"Feed archive: {args.feed_archive.resolve()}")
    print(f"Output path: {args.output_path.resolve()}")
    print(f"Pair: {args.pair}")
    print(f"Dry run: {'ON' if args.dry_run else 'OFF'}")
    print(f"Analysis records scanned: {counters['analysis_records']}")
    print(f"New seed rows: {len(new_seed_records)}")
    print(f"Skipped ineligible: {counters['skipped_ineligible']}")
    print(f"Skipped existing event_id: {counters['skipped_existing_event_id']}")
    print(f"Skipped existing seed: {counters['skipped_existing_seed']}")

    if new_seed_records:
        sample = new_seed_records[0]
        print(
            "Sample seed: "
            f"headline={sample['headline']!r}, published_at={sample['published_at']}, source={sample['source']}"
        )


def _analysis_to_seed_record(
    analysis_record: dict[str, Any],
    *,
    feed_index: dict[str, dict[str, Any]],
    pair: str,
) -> dict[str, Any] | None:
    event_id = _clean_str(analysis_record.get("event_id"))
    if event_id is None:
        return None

    affected_symbols = [_normalize_pair(symbol) for symbol in analysis_record.get("affected_symbols", []) if _clean_str(symbol)]
    normalized_pair = _normalize_pair(pair)
    if affected_symbols and normalized_pair not in affected_symbols:
        return None

    feed_payload = analysis_record.get("feed")
    if not isinstance(feed_payload, dict):
        feed_payload = feed_index.get(event_id)
    if not isinstance(feed_payload, dict):
        return None

    headline = _clean_str(feed_payload.get("headline"))
    published_at = _parse_datetime(feed_payload.get("timestamp")) or _parse_datetime(analysis_record.get("produced_at"))
    if headline is None or published_at is None:
        return None

    source = _resolve_source_type(feed_payload)
    source_url = _clean_str(feed_payload.get("url"))
    event_category = _clean_str(feed_payload.get("category"))
    macro_event_type = _infer_macro_event_type(analysis_record)
    sentiment_score = _safe_float(analysis_record.get("sentiment_score"), default=0.0)

    source_metadata = {
        "archive_origin": "arbex_live_analysis",
        "archive_timestamp_origin": "feed.timestamp" if feed_payload.get("timestamp") else "analysis.produced_at",
        "analysis_confidence": _stringify_value(analysis_record.get("confidence")),
        "trading_signal": _stringify_value(analysis_record.get("trading_signal")),
        "provider": _stringify_value(feed_payload.get("provider")),
        "feed_mode": _stringify_value(feed_payload.get("feed_mode")),
        "provenance": _stringify_value(feed_payload.get("provenance")),
        "upstream_status": _stringify_value(feed_payload.get("upstream_status")),
        "event_id": event_id,
        "source": source.value,
    }
    source_metadata = {key: value for key, value in source_metadata.items() if value}

    return {
        "event_id": event_id,
        "headline": headline,
        # Keep body empty here to avoid leaking model-generated rationales into training text.
        "body": None,
        "published_at": published_at.isoformat(),
        "source": source.value,
        "sentiment_label": sentiment_score,
        "pair": normalized_pair,
        "symbols": [normalized_pair],
        "source_metadata": source_metadata,
        "event_category": event_category,
        "macro_event_type": macro_event_type,
        "calendar_source": None,
        "source_url": source_url,
        "scheduled": False,
    }


def _build_feed_index(feed_records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for record in feed_records:
        event_id = _clean_str(record.get("event_id"))
        if event_id is None:
            continue
        if event_id not in index:
            index[event_id] = record
    return index


def _load_existing_seed_identity(path: Path) -> tuple[set[tuple[str, str]], set[str]]:
    if not path.exists():
        return set(), set()
    seed_keys: set[tuple[str, str]] = set()
    event_ids: set[str] = set()
    for record in _read_jsonl(path):
        event_id = _clean_str(record.get("event_id"))
        if event_id is not None:
            event_ids.add(event_id)
        try:
            seed_keys.add(_seed_identity(record))
        except KeyError:
            continue
    return seed_keys, event_ids


def _seed_identity(record: dict[str, Any]) -> tuple[str, str]:
    """Identity of a news event for training: headline + pair.

    published_at is deliberately NOT in the key. The live feed replays the same
    headline repeatedly and stamps each replay with a fresh timestamp, so including
    it meant the key changed every time and dedup never fired -- one headline reached
    the training table 322 times, and a train/test split would then put copies of the
    same headline on both sides.

    Excluding it is also a correctness fix, not merely deduplication: the label is a
    forward price return *from publication*, so a replay timestamp hours later
    measures a different window entirely. Callers must process records oldest-first so
    the surviving seed carries the earliest, truest publication time.
    """
    return (
        _normalize_headline(str(record["headline"])),
        _normalize_pair(str(record.get("pair", DEFAULT_PAIR))),
    )


def _analysis_sort_key(record: dict[str, Any]) -> str:
    """Oldest-first ordering key, so the earliest sighting of a headline wins."""
    feed = record.get("feed") if isinstance(record.get("feed"), dict) else {}
    return str(feed.get("timestamp") or record.get("produced_at") or "")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _parse_datetime(value: object) -> datetime | None:
    cleaned = _clean_str(value)
    if cleaned is None:
        return None
    parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _resolve_source_type(feed_payload: dict[str, Any]) -> SourceType:
    provider = _clean_str(feed_payload.get("provider"))
    raw_source = _clean_str(feed_payload.get("source"))
    for candidate in (provider, raw_source):
        if candidate is None:
            continue
        normalized = candidate.split("(", maxsplit=1)[0].strip().replace("-", "_").replace(" ", "_").upper()
        try:
            return SourceType(normalized)
        except ValueError:
            continue
    return SourceType.RSS


def _infer_macro_event_type(analysis_record: dict[str, Any]) -> str | None:
    evidence = analysis_record.get("evidence")
    if not isinstance(evidence, list):
        return None
    for item in evidence:
        if not isinstance(item, dict):
            continue
        macro_event_type = _clean_str(item.get("macro_event_type"))
        if macro_event_type is not None:
            return macro_event_type
    return None


def _normalize_pair(value: str) -> str:
    normalized = value.strip().upper().replace("_", "/")
    if "/" in normalized:
        return normalized
    if len(normalized) == 6 and normalized.isalpha():
        return f"{normalized[:3]}/{normalized[3:]}"
    return normalized or DEFAULT_PAIR


def _normalize_headline(value: str) -> str:
    return " ".join(value.casefold().split())


def _safe_float(value: object, *, default: float) -> float:
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _stringify_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _clean_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    stripped = value.strip()
    return stripped or None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export deduplicated timestamped event seeds from the persisted Arbex live archive.",
    )
    parser.add_argument("--analysis-archive", type=Path, help="Default: sentiment_assets/runtime/<slug>/live_analysis.jsonl")
    parser.add_argument("--feed-archive", type=Path, help="Default: sentiment_assets/runtime/<slug>/live_feed.jsonl")
    parser.add_argument("--output-path", type=Path, help="Default: sentiment_assets/<slug>/<slug>_live_archive_event_seeds.jsonl")
    parser.add_argument("--pair", default=DEFAULT_PAIR)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    return resolve_pair_defaults(parser.parse_args())


def resolve_pair_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """Archive and output paths follow --pair (the bridge archives each pair separately)."""
    runtime = runtime_archive_dir(args.pair)
    args.analysis_archive = args.analysis_archive or runtime / "live_analysis.jsonl"
    args.feed_archive = args.feed_archive or runtime / "live_feed.jsonl"
    args.output_path = args.output_path or sentiment_assets_dir(args.pair) / f"{pair_slug(args.pair)}_live_archive_event_seeds.jsonl"
    return args


if __name__ == "__main__":
    main()