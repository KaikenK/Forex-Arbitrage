"""Deterministic tests for the scale backbone (tagging, GDELT parse, event store). No network."""
from __future__ import annotations

from datetime import UTC, datetime

from research.data.base import EventStore, RawEvent
from research.data.gdelt import parse_gdelt_articles
from research.data.tagging import currencies_to_pairs, tag_categories, tag_currencies


# ---------------------------------------------------------------- tagging

def test_tag_currencies_and_pairs():
    text = "ECB holds rates as euro steadies; Fed signals caution"
    currencies = tag_currencies(text)
    assert "EUR" in currencies and "USD" in currencies
    assert currencies_to_pairs(currencies) == ["EUR/USD"]  # USD-only maps to nothing


def test_tag_categories_multilabel():
    labels = tag_categories("ECB rate decision: 25 bps hike as inflation stays hot")
    assert "rate_decision" in labels
    assert "central_bank" in labels
    assert "inflation" in labels


# ---------------------------------------------------------------- GDELT parse

def test_parse_gdelt_articles_tags_and_filters():
    payload = {"articles": [
        {"url": "https://x.com/a", "title": "ECB raises rates as euro climbs",
         "seendate": "20240110T230000Z", "domain": "x.com", "sourcecountry": "US", "language": "English"},
        {"url": "https://x.com/b", "title": "Local weather stays mild this weekend",
         "seendate": "20240110T231500Z", "domain": "x.com"},  # no currency -> dropped
        {"url": "", "title": "missing url", "seendate": "20240110T233000Z"},  # invalid -> dropped
    ]}
    events = parse_gdelt_articles(payload)
    assert len(events) == 1
    event = events[0]
    assert event.timestamp == datetime(2024, 1, 10, 23, 0, tzinfo=UTC)
    assert "EUR" in event.currencies
    assert "rate_decision" in event.categories
    assert event.source == "gdelt"


# ---------------------------------------------------------------- store

def _raw(event_id: str, pair_currency: str = "EUR") -> RawEvent:
    return RawEvent(
        event_id=event_id,
        timestamp=datetime(2024, 1, 10, 23, 0, tzinfo=UTC),
        headline="ECB rate decision",
        source="gdelt",
        currencies=(pair_currency,),
        categories=("rate_decision", "central_bank"),
    )


def test_event_store_dedup_and_manifest(tmp_path):
    store = EventStore(tmp_path / "events.parquet")
    assert store.append([_raw("a"), _raw("b")]) == 2
    assert store.append([_raw("b"), _raw("c")]) == 1  # b already present
    frame = store.load()
    assert len(frame) == 3
    assert set(frame["event_id"]) == {"a", "b", "c"}
    assert store.manifest_path.exists()


def test_raw_event_to_study_events():
    raw = RawEvent(
        event_id="e1",
        timestamp=datetime(2024, 1, 10, 23, 0, tzinfo=UTC),
        headline="ECB and Fed diverge; yen slides",
        source="gdelt",
        currencies=("EUR", "USD", "JPY"),
        categories=("central_bank",),
    )
    study = raw.to_study_events()
    pairs = {event.pair for event in study}
    assert pairs == {"EUR/USD", "USD/JPY"}  # USD alone excluded
    assert all(event.categories == ("central_bank",) for event in study)
