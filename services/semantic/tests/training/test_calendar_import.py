from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from urllib.error import HTTPError

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from training.calendar_import import calendar_row_to_seed
from training.calendar_import import classify_calendar_row
from training.calendar_import import fetch_usdinr_calendar_rows
from training.calendar_import import merge_seed_records
from training.calendar_import import parse_numeric_value


def test_parse_numeric_value_handles_suffixes_and_percentages() -> None:
    assert parse_numeric_value("256K", unit="K") == 256_000.0
    assert parse_numeric_value("4.1%", unit="%") == 4.1
    assert parse_numeric_value("C$-1.13B", unit="B") == -1_130_000_000.0


def test_calendar_row_to_seed_maps_nonfarm_payrolls_event() -> None:
    row = {
        "CalendarId": "368910",
        "Date": "2025-01-10T13:30:00",
        "Country": "United States",
        "Category": "Non Farm Payrolls",
        "Event": "Non Farm Payrolls",
        "Reference": "Dec",
        "Source": "U.S. Bureau of Labor Statistics",
        "SourceURL": "http://www.bls.gov/",
        "Actual": "256K",
        "Forecast": "160K",
        "Previous": "212K",
        "URL": "/united-states/non-farm-payrolls",
        "LastUpdate": "2025-01-10T13:33:02.96",
        "Unit": "K",
        "Ticker": "NFP TCH",
        "Symbol": "NFP TCH",
    }

    rule = classify_calendar_row(row)
    assert rule is not None

    seed = calendar_row_to_seed(row, rule)

    assert seed["event_id"] == "usdinr-us-nfp-2025-01-10-368910"
    assert seed["macro_event_type"] == "nfp"
    assert seed["event_category"] == "macro"
    assert seed["actual"] == 256_000.0
    assert seed["forecast"] == 160_000.0
    assert seed["previous"] == 212_000.0
    assert seed["standardized_surprise"] == 0.6
    assert seed["source_metadata"]["calendar_country"] == "United States"


def test_merge_seed_records_preserves_existing_curated_duplicates() -> None:
    existing_records = [{"event_id": "evt-1", "headline": "curated", "published_at": "2025-01-01T00:00:00+00:00"}]
    imported_records = [
        {"event_id": "evt-1", "headline": "imported", "published_at": "2025-01-01T00:00:00+00:00"},
        {"event_id": "evt-2", "headline": "fresh", "published_at": "2025-01-02T00:00:00+00:00"},
    ]

    merged = merge_seed_records(existing_records, imported_records)

    assert [record["event_id"] for record in merged] == ["evt-1", "evt-2"]
    assert merged[0]["headline"] == "curated"


def test_fetch_usdinr_calendar_rows_deduplicates_daily_sources() -> None:
    us_row = {
        "CalendarId": "100",
        "Date": "2025-01-10T13:30:00",
        "Country": "United States",
        "Category": "Non Farm Payrolls",
        "Event": "Non Farm Payrolls",
    }
    india_row = {
        "CalendarId": "200",
        "Date": "2025-01-10T10:30:00",
        "Country": "India",
        "Category": "Inflation Rate",
        "Event": "Inflation Rate YoY",
    }

    def fake_fetch_json(url: str) -> object:
        if "country/united%20states" in url:
            return [dict(us_row)]
        return [dict(india_row), dict(us_row)]

    rows = fetch_usdinr_calendar_rows(
        date(2025, 1, 10),
        date(2025, 1, 10),
        fetch_json=fake_fetch_json,
    )

    assert [(row["Country"], row["CalendarId"]) for row in rows] == [
        ("India", "200"),
        ("United States", "100"),
    ]


def test_fetch_usdinr_calendar_rows_skips_forbidden_days() -> None:
    us_row = {
        "CalendarId": "100",
        "Date": "2025-01-10T13:30:00",
        "Country": "United States",
        "Category": "Non Farm Payrolls",
        "Event": "Non Farm Payrolls",
    }

    def fake_fetch_json(url: str) -> object:
        if "country/All" in url:
            raise HTTPError(url, 403, "Forbidden", hdrs=None, fp=None)
        return [dict(us_row)]

    rows = fetch_usdinr_calendar_rows(
        date(2025, 1, 10),
        date(2025, 1, 10),
        fetch_json=fake_fetch_json,
    )

    assert [(row["Country"], row["CalendarId"]) for row in rows] == [("United States", "100")]