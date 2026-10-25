"""Deterministic loader tests (no network)."""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from research.data import frame_to_series, load_events


def test_load_events_parses_schema(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        '{"event_id":"e1","published_at":"2025-01-10T13:30:00+00:00","symbols":["USD/INR"],'
        '"event_category":"macro","macro_event_type":"nfp","standardized_surprise":0.6,'
        '"headline":"NFP beats"}\n'
        '{"bad json\n'
        '{"event_id":"e2","published_at":"2025-02-01T00:00:00Z","event_category":"central-bank"}\n',
        encoding="utf-8",
    )
    events = load_events(path)

    assert len(events) == 2
    first = events[0]
    assert first.event_id == "e1"
    assert first.pair == "USD/INR"
    assert first.categories == ("macro", "nfp")
    assert first.surprise == 0.6
    assert first.timestamp == datetime(2025, 1, 10, 13, 30, tzinfo=UTC)
    # Z-suffix parsed, missing symbols -> default pair
    assert events[1].pair == "USD/INR"
    assert events[1].categories == ("central-bank",)


def test_frame_to_series_utc_and_close_hour():
    frame = pd.DataFrame(
        {"Close": [82.5, 82.7, 82.6]},
        index=pd.to_datetime(["2025-01-08", "2025-01-09", "2025-01-10"]),
    )
    series = frame_to_series(frame, "USD/INR", close_hour_utc=21)

    assert series.pair == "USD/INR"
    assert len(series.times) == 3
    assert all(t.tzinfo is not None for t in series.times)
    assert series.times[0] == datetime(2025, 1, 8, 21, 0, tzinfo=UTC)
    assert series.prices[1] == 82.7
