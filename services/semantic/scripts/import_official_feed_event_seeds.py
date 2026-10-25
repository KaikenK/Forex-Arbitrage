"""Import official speech and policy feed items into a timestamped USD/INR seed ledger."""
from __future__ import annotations

import argparse
import calendar
import json
import site
import sys
import time
import tomllib
from collections import Counter
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import feedparser  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
USER_SITE_PATH = Path(site.getusersitepackages())
for import_path in (str(USER_SITE_PATH), str(SRC_PATH)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from news.analyzer.context_tagger import ContextTag
from news.analyzer.context_tagger import ContextTagger
from news.models import SourceType

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT.parent / "Forex-Arbitrage" / "sentiment_assets" / "usdinr" / "usdinr_official_policy_event_seeds.jsonl"
DEFAULT_PAIR = "USD/INR"


def main() -> None:
    args = _parse_args()
    config = tomllib.loads(args.config_path.read_text(encoding="utf-8"))
    official_config = config.get("official_feeds", {})
    feed_urls = list(official_config.get("feed_urls", []))
    if not feed_urls:
        raise RuntimeError("No official feed URLs are configured.")

    context_tagger = ContextTagger()
    existing_identities = _existing_seed_identities(args.output_path.resolve())
    emitted_identities: set[tuple[str, str]] = set()
    source_counter: Counter[str] = Counter()
    category_counter: Counter[str] = Counter()
    new_seed_records: list[dict[str, Any]] = []

    for feed_url in feed_urls:
        parsed_feed = feedparser.parse(feed_url)
        entries = getattr(parsed_feed, "entries", [])
        if not isinstance(entries, list):
            continue
        for raw_entry in entries[: args.max_entries_per_feed]:
            entry = dict(raw_entry) if not isinstance(raw_entry, dict) else raw_entry
            seed_record = _entry_to_seed_record(entry, feed_url=feed_url, pair=args.pair, context_tagger=context_tagger)
            if seed_record is None:
                continue
            identity = (str(seed_record["event_id"]), str(seed_record["published_at"]))
            if identity in existing_identities or identity in emitted_identities:
                continue
            emitted_identities.add(identity)
            new_seed_records.append(seed_record)
            source_counter[str(seed_record["source"])] += 1
            category_counter[str(seed_record.get("event_category") or "unknown")] += 1

    if args.limit is not None:
        new_seed_records = new_seed_records[: args.limit]

    if new_seed_records:
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        with args.output_path.open("a", encoding="utf-8") as handle:
            for seed_record in new_seed_records:
                handle.write(json.dumps(seed_record, ensure_ascii=True))
                handle.write("\n")

    print(f"Config path: {args.config_path.resolve()}")
    print(f"Output path: {args.output_path.resolve()}")
    print(f"Feed URLs scanned: {len(feed_urls)}")
    print(f"New official seed rows: {len(new_seed_records)}")
    print(f"Source breakdown: {dict(sorted(source_counter.items()))}")
    print(f"Category breakdown: {dict(sorted(category_counter.items()))}")
    if new_seed_records:
        sample = new_seed_records[0]
        print(f"Sample official seed: headline={sample['headline']!r}, published_at={sample['published_at']}")


def _entry_to_seed_record(
    entry: dict[str, Any],
    *,
    feed_url: str,
    pair: str,
    context_tagger: ContextTagger,
) -> dict[str, Any] | None:
    link = _clean_str(entry.get("link"))
    headline = _clean_str(entry.get("title"))
    if link is None or headline is None:
        return None
    published_at = _published_at(entry)
    summary = _clean_str(entry.get("summary"))
    context_tag = context_tagger.tag(headline)
    event_category = _event_category_from_tag(context_tag)
    macro_event_type = _macro_event_type(headline, context_tag)
    source_metadata = {
        "event_id": link,
        "source": SourceType.OFFICIAL.value,
        "ingestion_family": "official_feeds",
        "feed_url": feed_url,
        "context_tag": context_tag.value,
    }
    return {
        "event_id": link,
        "headline": headline,
        "body": summary,
        "published_at": published_at.isoformat(),
        "source": SourceType.OFFICIAL.value,
        "sentiment_label": 0.0,
        "pair": pair,
        "symbols": [pair],
        "source_metadata": source_metadata,
        "event_category": event_category,
        "macro_event_type": macro_event_type,
        "source_url": link,
        "scheduled": False,
    }


def _published_at(entry: dict[str, Any]) -> datetime:
    published_parsed = entry.get("published_parsed")
    if isinstance(published_parsed, time.struct_time):
        return datetime.fromtimestamp(calendar.timegm(published_parsed), UTC)
    updated_parsed = entry.get("updated_parsed")
    if isinstance(updated_parsed, time.struct_time):
        return datetime.fromtimestamp(calendar.timegm(updated_parsed), UTC)
    return datetime.now(UTC)


def _event_category_from_tag(context_tag: ContextTag) -> str:
    if context_tag == ContextTag.CENTRAL_BANK:
        return "central-bank"
    if context_tag == ContextTag.POLICY:
        return "policy"
    if context_tag == ContextTag.FISCAL:
        return "fiscal"
    return "official"


def _macro_event_type(headline: str, context_tag: ContextTag) -> str:
    normalized = headline.casefold()
    if "speech" in normalized:
        return "official-speech"
    if "statement" in normalized or "minutes" in normalized:
        return "policy-statement"
    if "monetary policy" in normalized or "interest rate" in normalized or "rate decision" in normalized:
        return "policy-decision"
    if context_tag == ContextTag.CENTRAL_BANK:
        return "central-bank-communication"
    if context_tag == ContextTag.POLICY:
        return "policy-headline"
    return "official-headline"


def _existing_seed_identities(path: Path) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    if not path.exists():
        return identities
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        event_id = _clean_str(payload.get("event_id"))
        published_at = _clean_str(payload.get("published_at"))
        if event_id is None or published_at is None:
            continue
        identities.add((event_id, published_at))
    return identities


def _clean_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    stripped = value.strip()
    return stripped or None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import official speech and policy feed items into a timestamped USD/INR seed ledger.",
    )
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--pair", default=DEFAULT_PAIR)
    parser.add_argument("--max-entries-per-feed", type=int, default=20)
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


if __name__ == "__main__":
    main()
