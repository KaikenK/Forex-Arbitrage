"""Tests for the economic-calendar / surprise connector. No network."""
from __future__ import annotations

from datetime import UTC, datetime

from research.data.calendar import (
    calendar_to_raw_events,
    parse_calendar_records,
    parse_number,
)


def test_parse_number_units_and_edges():
    assert parse_number("256K") == 256_000.0
    assert parse_number("1.2M") == 1_200_000.0
    assert parse_number("3.5%") == 3.5
    assert parse_number("4,500") == 4500.0
    assert parse_number("-0.3") == -0.3
    assert parse_number("") is None
    assert parse_number("n/a") is None


def _records():
    # Same event repeated so surprise standardization has a population.
    return [
        {"date": "2024-01-05", "time": "13:30", "currency": "USD", "event": "Non-Farm Payrolls",
         "importance": "High", "previous": "150K", "forecast": "160K", "actual": "256K"},
        {"date": "2024-02-02", "time": "13:30", "currency": "USD", "event": "Non-Farm Payrolls",
         "importance": "High", "previous": "256K", "forecast": "180K", "actual": "170K"},
        {"date": "2024-03-01", "time": "13:30", "currency": "USD", "event": "Non-Farm Payrolls",
         "importance": "High", "previous": "170K", "forecast": "190K", "actual": "200K"},
    ]


def test_parse_calendar_surprise_and_standardization():
    events = parse_calendar_records(_records())
    assert len(events) == 3
    first = events[0]
    assert first.currency == "USD"
    assert first.country == "United States"
    assert first.actual == 256_000.0
    assert first.surprise == 96_000.0            # 256K - 160K
    assert first.timestamp == datetime(2024, 1, 5, 13, 30, tzinfo=UTC)
    # standardized surprise computed (>=2 obs, nonzero std)
    assert first.standardized_surprise is not None
    assert first.standardized_surprise > 0       # positive surprise


def test_calendar_tz_offset_to_utc():
    # Source stamped in UTC-5 (US Eastern winter); should shift +5h to UTC.
    events = parse_calendar_records(
        [{"date": "2024-01-05", "time": "08:30", "currency": "USD", "event": "CPI",
          "forecast": "3.2%", "actual": "3.4%"}],
        tz_offset_hours=-5.0,
    )
    assert events[0].timestamp == datetime(2024, 1, 5, 13, 30, tzinfo=UTC)


def test_iso_datetime_column_with_offset_converts_to_utc():
    """The real dump ships an ISO `DateTime` with its own offset, not date/time columns."""
    events = parse_calendar_records([
        {"DateTime": "2018-01-05T13:30:00+03:30", "Currency": "USD",
         "Event": "Non-Farm Payrolls", "Impact": "High Impact Expected",
         "Actual": "256K", "Forecast": "160K", "Previous": "150K"},
    ])
    assert len(events) == 1
    event = events[0]
    # 13:30 at +03:30 == 10:00 UTC; the embedded offset wins over any tz_offset argument.
    assert event.timestamp == datetime(2018, 1, 5, 10, 0, tzinfo=UTC)
    assert event.currency == "USD"
    assert event.importance == "high"
    assert event.surprise == 96_000.0
    assert event.has_exact_time


def test_date_only_rows_are_flagged_not_treated_as_exact():
    """23:59:59 is a date-only placeholder, not a real release time."""
    events = parse_calendar_records([
        {"DateTime": "2018-03-01T23:59:59+03:30", "Currency": "EUR",
         "Event": "German Retail Sales", "Impact": "Low Impact Expected"},
        {"DateTime": "2018-03-01T08:30:00+03:30", "Currency": "EUR",
         "Event": "ECB Press Conference", "Impact": "High Impact Expected"},
    ])
    by_name = {event.event_name: event for event in events}
    assert by_name["German Retail Sales"].timestamp_precision_minutes == 1440.0
    assert not by_name["German Retail Sales"].has_exact_time
    assert by_name["ECB Press Conference"].has_exact_time


def test_impact_normalization():
    events = parse_calendar_records([
        {"DateTime": "2018-01-05T13:30:00+00:00", "Currency": "USD", "Event": "A",
         "Impact": "Medium Impact Expected"},
        {"DateTime": "2018-01-05T13:30:00+00:00", "Currency": "USD", "Event": "B",
         "Impact": "Non-Economic"},
    ])
    assert [event.importance for event in events] == ["medium", "non-economic"]


def test_precision_survives_conversion_to_raw_event():
    events = parse_calendar_records([
        {"DateTime": "2018-03-01T23:59:59+00:00", "Currency": "EUR", "Event": "X"},
    ])
    raw = calendar_to_raw_events(events)[0]
    assert raw.timestamp_precision_minutes == 1440.0
    assert raw.to_row()["timestamp_precision_minutes"] == 1440.0
    # and it reaches the event-study Event objects used for alignment
    assert raw.to_study_events()[0].timestamp_precision_minutes == 1440.0


def test_calendar_to_raw_events_marks_scheduled():
    raws = calendar_to_raw_events(parse_calendar_records(_records()))
    assert raws
    raw = raws[0]
    assert raw.event_type == "scheduled"
    assert raw.scheduled is True
    assert raw.currencies == ("USD",)
    assert raw.event_id.startswith("cal-")
    # NFP name maps to the employment category via the tagger
    assert "employment" in raw.categories
