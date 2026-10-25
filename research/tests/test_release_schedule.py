"""Calendar timestamp repair: stale Tehran offsets, midnight placeholders, release clocks.

The cases are taken from the real dump (RESEARCH_TRACKER §6, 2026-10-24).
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from research.data.calendar import FOREXFACTORY_SOURCE_ZONE, parse_calendar_records
from research.data.release_schedule import (
    MIN_TIMED_ROWS,
    family_for,
    impute_release_times,
    validate_release_clocks,
)

TEHRAN = FOREXFACTORY_SOURCE_ZONE


def _row(stamp: str, event: str, currency: str = "USD") -> dict:
    return {"DateTime": stamp, "Currency": currency, "Event": event, "Impact": "High Impact Expected",
            "Actual": "1", "Forecast": "0"}


def _claims_history(n: int, *, start: date = date(2010, 1, 7)) -> list[dict]:
    """`n` weekly Claims rows with observed times: 16:00 Tehran winter == 08:30 EST."""
    rows = []
    day = start
    while len(rows) < n:
        if day.month in (12, 1, 2):  # both sides on standard time: 08:30 EST = 17:00 +03:30
            rows.append(_row(f"{day.isoformat()}T17:00:00+03:30", "Unemployment Claims"))
        day += timedelta(days=7)
    return rows


def test_stale_iranian_dst_offset_is_corrected() -> None:
    """2023-06-01 Claims: 16:00 Tehran is 12:30 UTC (08:30 EDT); the dump says +04:30."""
    [event] = parse_calendar_records([_row("2023-06-01T16:00:00+04:30", "Unemployment Claims")],
                                     source_zone=TEHRAN)
    assert event.timestamp == datetime(2023, 6, 1, 12, 30, tzinfo=UTC)


def test_correct_historical_offsets_are_reproduced() -> None:
    """Before 2022 Iran observed DST, so the dump's +04:30 was right and must not change."""
    stamp = "2018-07-06T17:00:00+04:30"
    [zoned] = parse_calendar_records([_row(stamp, "Core CPI m/m")], source_zone=TEHRAN)
    [embedded] = parse_calendar_records([_row(stamp, "Core CPI m/m")])
    assert zoned.timestamp == embedded.timestamp == datetime(2018, 7, 6, 12, 30, tzinfo=UTC)


def test_midnight_is_a_missing_time_not_a_release_time() -> None:
    """NFP 2018-01-05 is stored at 00:00 local -> would be Thu 20:30 UTC if trusted."""
    [event] = parse_calendar_records([_row("2018-01-05T00:00:00+03:30", "Non-Farm Employment Change")],
                                     source_zone=TEHRAN)
    assert not event.has_exact_time
    assert event.source_local_date == date(2018, 1, 5), "the date survives even though UTC is the 4th"


def test_validated_family_fills_missing_payrolls_time_dst_aware() -> None:
    """Unemployment Claims' observed times validate the clock; NFP itself only needs a family."""
    history = _claims_history(MIN_TIMED_ROWS)
    payrolls = [
        _row("2018-01-05T00:00:00+03:30", "Non-Farm Employment Change"),   # winter: 13:30 UTC
        _row("2020-06-05T00:00:00+04:30", "Non-Farm Employment Change"),   # summer: 12:30 UTC
        # Employment Situation needs its own observed rows to validate:
        *[_row(f"{d.isoformat()}T17:00:00+03:30", "Non-Farm Employment Change")
          for d in (date(2009, 1, 9) + timedelta(days=364 * k) for k in range(MIN_TIMED_ROWS))],
    ]
    events = parse_calendar_records(history + payrolls, source_zone=TEHRAN, impute_schedule=True)
    filled = {e.timestamp: e for e in events if e.timestamp_source == "schedule"}

    assert datetime(2018, 1, 5, 13, 30, tzinfo=UTC) in filled
    assert datetime(2020, 6, 5, 12, 30, tzinfo=UTC) in filled
    assert all(e.has_exact_time for e in filled.values())
    assert filled[datetime(2018, 1, 5, 13, 30, tzinfo=UTC)].timestamp.weekday() == 4, "a Friday"


def test_family_without_enough_observed_rows_stays_date_only() -> None:
    """Retail Sales has no observed times in this sample, so its clock is unconfirmed."""
    events = parse_calendar_records(
        _claims_history(MIN_TIMED_ROWS) + [_row("2019-03-11T00:00:00+03:30", "Retail Sales m/m")],
        source_zone=TEHRAN, impute_schedule=True,
    )
    [retail] = [e for e in events if e.event_name == "Retail Sales m/m"]
    assert not retail.has_exact_time and retail.timestamp_source == "source"


def test_family_whose_observed_times_disagree_is_not_validated() -> None:
    """If observed times contradict the documented clock, the clock is not trusted."""
    rows = [_row(f"{(date(2012, 1, 3) + timedelta(days=30 * k)).isoformat()}T18:00:00+03:30",
                 "ISM Manufacturing PMI") for k in range(MIN_TIMED_ROWS)]   # 09:30 ET, not 10:00
    events = parse_calendar_records(rows, source_zone=TEHRAN)
    check = validate_release_clocks(events)["ISM Manufacturing"]
    assert check.timed_rows == MIN_TIMED_ROWS and not check.validated


def test_observed_time_is_never_overwritten() -> None:
    """A row with a real (even unusual) time keeps it; imputation only fills gaps."""
    odd = _row("2015-08-03T18:15:00+04:30", "ISM Manufacturing PMI")   # 09:45 ET, as in the dump
    history = [_row(f"{(date(2012, 1, 3) + timedelta(days=30 * k)).isoformat()}T18:30:00+03:30",
                    "ISM Manufacturing PMI") for k in range(MIN_TIMED_ROWS)]
    events = impute_release_times(parse_calendar_records(history + [odd], source_zone=TEHRAN))
    [kept] = [e for e in events if e.source_local_date == date(2015, 8, 3)]
    assert kept.timestamp == datetime(2015, 8, 3, 13, 45, tzinfo=UTC)
    assert kept.timestamp_source == "source"


def test_unscheduled_and_speech_events_are_never_imputed() -> None:
    assert family_for("USD", "Fed Chair Powell Speaks") is None
    assert family_for("USD", "FOMC Statement") is None
    assert family_for("EUR", "CPI m/m") is None, "families are per currency"
    [speech] = parse_calendar_records([_row("2021-03-04T00:00:00+03:30", "Fed Chair Powell Speaks")],
                                      source_zone=TEHRAN, impute_schedule=True)
    assert not speech.has_exact_time


def test_timestamp_source_reaches_the_event_store_row() -> None:
    from research.data.calendar import calendar_to_raw_events

    events = parse_calendar_records(_claims_history(MIN_TIMED_ROWS) +
                                    [_row("2019-01-03T00:00:00+03:30", "Unemployment Claims")],
                                    source_zone=TEHRAN, impute_schedule=True)
    rows = [raw.to_row() for raw in calendar_to_raw_events(events)]
    assert {row["timestamp_source"] for row in rows} == {"source", "schedule"}
