from __future__ import annotations

import json
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from training.models import PriceBar
from training.usdinr_dataset_builder import DEFAULT_PAIR
from training.usdinr_dataset_builder import STANDARDIZED_SURPRISE_FORMULA
from training.usdinr_dataset_builder import build_training_rows_from_events
from training.usdinr_dataset_builder import load_timestamped_event_seeds


def test_load_timestamped_event_seeds_preserves_real_publish_time(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        json.dumps(
            {
                "event_id": "evt-1",
                "headline": "RBI holds repo rate steady",
                "published_at": "2026-03-25T12:00:30+00:00",
                "source": "rss",
                "sentiment_label": 0.25,
                "event_category": "central-bank",
                "macro_event_type": "rate-decision",
                "scheduled": True,
                "actual": 6.25,
                "forecast": 6.25,
                "previous": 6.50,
                "calendar_source": "rbi.org.in",
                "source_url": "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12774",
            },
        )
        + "\n",
        encoding="utf-8",
    )

    seeds = load_timestamped_event_seeds(events_path)

    assert len(seeds) == 1
    assert seeds[0].published_at == datetime(2026, 3, 25, 12, 0, 30, tzinfo=UTC)
    assert seeds[0].symbols == [DEFAULT_PAIR]
    assert seeds[0].scheduled is True
    assert seeds[0].actual == 6.25
    assert seeds[0].forecast == 6.25
    assert seeds[0].previous == 6.50
    assert seeds[0].standardized_surprise == 0.0
    assert seeds[0].calendar_source == "rbi.org.in"
    assert seeds[0].source_url == "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12774"
    assert seeds[0].source_metadata == {
        "event_category": "central-bank",
        "macro_event_type": "rate-decision",
        "scheduled": "true",
        "actual": "6.25",
        "forecast": "6.25",
        "previous": "6.5",
        "standardized_surprise": "0.0",
        "surprise_formula": STANDARDIZED_SURPRISE_FORMULA,
        "calendar_source": "rbi.org.in",
        "source_url": "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12774",
        "event_id": "evt-1",
        "source": "RSS",
    }


def test_load_timestamped_event_seeds_derives_nonzero_standardized_surprise(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        json.dumps(
            {
                "event_id": "evt-nfp",
                "headline": "U.S. payroll growth beats consensus",
                "published_at": "2025-01-10T13:30:00+00:00",
                "source": "rss",
                "sentiment_label": 0.0,
                "event_category": "macro",
                "macro_event_type": "nfp",
                "scheduled": True,
                "actual": 256000,
                "forecast": 160000,
                "previous": 212000,
                "calendar_source": "https://api.tradingeconomics.com/calendar/calendarid/368910?c=guest:guest",
            },
        )
        + "\n",
        encoding="utf-8",
    )

    seeds = load_timestamped_event_seeds(events_path)

    assert len(seeds) == 1
    assert seeds[0].standardized_surprise == 0.6
    assert seeds[0].source_metadata["standardized_surprise"] == "0.6"
    assert seeds[0].source_metadata["surprise_formula"] == STANDARDIZED_SURPRISE_FORMULA


def test_load_timestamped_event_seeds_deduplicates_repeated_release_entries(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_id": "evt-cpi-primary",
                        "headline": "India Inflation Rate YoY released at 5.22% versus 5.30% forecast",
                        "published_at": "2025-01-13T10:30:00+00:00",
                        "symbols": ["USD/INR"],
                        "source": "rss",
                        "source_url": "https://www.mospi.gov.in/",
                    },
                ),
                json.dumps(
                    {
                        "event_id": "evt-cpi-duplicate",
                        "headline": "India Inflation Rate YoY released at 5.22% versus 5.30% forecast!!!",
                        "published_at": "2025-01-13T10:30:00+00:00",
                        "symbols": ["USD/INR"],
                        "source": "rss",
                        "source_url": "https://www.mospi.gov.in/",
                    },
                ),
            ],
        )
        + "\n",
        encoding="utf-8",
    )

    seeds = load_timestamped_event_seeds(events_path)

    assert len(seeds) == 1
    assert seeds[0].event_id == "evt-cpi-primary"


def test_build_training_rows_from_events_uses_timestamped_events() -> None:
    event_seeds = load_timestamped_event_seeds_from_objects(
        [
            {
                "event_id": "evt-1",
                "headline": "RBI holds repo rate steady",
                "published_at": "2026-03-25T12:00:30+00:00",
                "source": "rss",
                "sentiment_label": 0.25,
                "event_category": "central-bank",
                "macro_event_type": "rate-decision",
                "scheduled": True,
                "actual": 6.25,
                "forecast": 6.25,
                "previous": 6.50,
                "calendar_source": "rbi.org.in",
            },
        ],
    )
    price_history = {
        DEFAULT_PAIR: [
            PriceBar(symbol=DEFAULT_PAIR, timestamp=datetime(2026, 3, 25, 13, 0, 0, tzinfo=UTC), close=83.10),
            PriceBar(symbol=DEFAULT_PAIR, timestamp=datetime(2026, 3, 25, 14, 0, 0, tzinfo=UTC), close=83.16),
            PriceBar(symbol=DEFAULT_PAIR, timestamp=datetime(2026, 3, 25, 17, 0, 0, tzinfo=UTC), close=83.35),
            PriceBar(symbol=DEFAULT_PAIR, timestamp=datetime(2026, 3, 26, 13, 0, 0, tzinfo=UTC), close=83.50),
        ],
    }

    rows = build_training_rows_from_events(
        event_seeds,
        price_history,
        [60, 240, 1440],
        candidate_symbols=[DEFAULT_PAIR],
    )

    assert [(row.pair, row.horizon_minutes) for row in rows] == [
        (DEFAULT_PAIR, 60),
        (DEFAULT_PAIR, 240),
        (DEFAULT_PAIR, 1440),
    ]
    assert all(row.timestamp == datetime(2026, 3, 25, 12, 0, 30, tzinfo=UTC) for row in rows)
    assert all(row.source_metadata["event_id"] == "evt-1" for row in rows)
    assert all(row.source_metadata["source"] == "RSS" for row in rows)
    assert all(row.event_category == "central-bank" for row in rows)
    assert all(row.macro_event_type == "rate-decision" for row in rows)
    assert all(row.scheduled is True for row in rows)
    assert all(row.actual == 6.25 for row in rows)
    assert all(row.forecast == 6.25 for row in rows)
    assert all(row.previous == 6.50 for row in rows)
    assert all(row.standardized_surprise == 0.0 for row in rows)
    assert all(row.calendar_source == "rbi.org.in" for row in rows)


def load_timestamped_event_seeds_from_objects(raw_events: list[dict[str, object]]):
    events_path = Path(__file__).resolve().parent / "_tmp_usdinr_events.jsonl"
    events_path.write_text(
        "".join(json.dumps(raw_event) + "\n" for raw_event in raw_events),
        encoding="utf-8",
    )
    try:
        return load_timestamped_event_seeds(events_path)
    finally:
        events_path.unlink(missing_ok=True)