"""Option A (RESEARCH_TRACKER §6): σ is estimated outside the pandemic window, applied to all."""
from __future__ import annotations

import pytest

from research.data.calendar import (
    PANDEMIC_WINDOW_END,
    PANDEMIC_WINDOW_START,
    calendar_to_raw_events,
    parse_calendar_records,
)


def _nfp(day: str, actual: str, forecast: str = "100K", clock: str = "13:30") -> dict:
    return {"DateTime": f"{day}T{clock}:00+00:00", "Currency": "USD", "Event": "Non-Farm Employment Change",
            "Impact": "High Impact Expected", "Actual": actual, "Forecast": forecast}


# Normal months: surprises of +-50K. One pandemic month: +10,000K.
ROWS = [
    _nfp("2019-01-04", "150K"), _nfp("2019-02-01", "50K"), _nfp("2019-03-08", "150K"),
    _nfp("2019-04-05", "50K"), _nfp("2020-06-05", "10100K"), _nfp("2021-09-03", "150K"),
]


def test_sigma_excludes_pandemic_surprises_but_all_releases_get_a_z() -> None:
    events = {e.timestamp.date().isoformat(): e for e in parse_calendar_records(ROWS)}
    outside = [50_000.0, -50_000.0, 50_000.0, -50_000.0, 50_000.0]
    mean = sum(outside) / len(outside)
    sigma = (sum((v - mean) ** 2 for v in outside) / (len(outside) - 1)) ** 0.5

    assert events["2019-01-04"].standardized_surprise == pytest.approx(50_000.0 / sigma)
    # The pandemic release is still standardized, by the non-pandemic σ: a huge z, not ~1.
    assert events["2020-06-05"].standardized_surprise == pytest.approx(10_000_000.0 / sigma)
    assert events["2020-06-05"].standardized_surprise > 100


def test_full_sample_sigma_would_have_flattened_normal_months() -> None:
    """The defect Option A fixes: with the outlier in σ, a normal month is ~0.01σ."""
    z_normal = parse_calendar_records(ROWS)[0].standardized_surprise
    assert z_normal > 0.9, "normal months stay near 1σ under Option A"


def test_window_flag_is_set_and_reaches_the_store_row() -> None:
    events = parse_calendar_records(ROWS + [_nfp("2020-03-01", "120K"), _nfp("2021-06-30", "120K"),
                                            _nfp("2021-07-01", "120K"), _nfp("2020-02-29", "120K")])
    flag = {e.timestamp.date().isoformat(): e.in_pandemic_window for e in events}
    assert flag["2020-06-05"] and flag["2020-03-01"] and flag["2021-06-30"], "window is inclusive"
    assert not flag["2021-07-01"] and not flag["2020-02-29"] and not flag["2019-01-04"]
    rows = {r["timestamp"].date().isoformat(): r for r in (raw.to_row() for raw in calendar_to_raw_events(events))}
    assert rows["2020-06-05"]["in_pandemic_window"] is True
    assert rows["2019-01-04"]["in_pandemic_window"] is False


def test_date_only_rows_use_their_source_date_for_window_membership() -> None:
    """A 00:00 Tehran placeholder for 2021-07-01 has a UTC stamp on 2021-06-30."""
    [event] = parse_calendar_records([{"DateTime": "2021-07-01T00:00:00+04:30", "Currency": "USD",
                                       "Event": "X", "Actual": "1", "Forecast": "0"}],
                                     source_zone="Asia/Tehran")
    assert event.timestamp.date().isoformat() == "2021-06-30"
    assert not event.in_pandemic_window


def test_group_with_only_pandemic_surprises_gets_no_z() -> None:
    events = parse_calendar_records([_nfp("2020-04-03", "10K"), _nfp("2020-05-08", "500K")])
    assert all(e.standardized_surprise is None for e in events)


def test_window_constants_are_the_preregistered_dates() -> None:
    assert (PANDEMIC_WINDOW_START.isoformat(), PANDEMIC_WINDOW_END.isoformat()) == ("2020-03-01", "2021-06-30")
