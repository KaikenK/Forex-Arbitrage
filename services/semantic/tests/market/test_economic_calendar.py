"""Live economic calendar: parsing (checked on a real response), proximity, caching. No network."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from market.currency_bias_tracker import CurrencyBiasTracker
from market.economic_calendar import (
    CACHE_FILE,
    MIN_FETCH_INTERVAL_SECONDS,
    RELEASE_WINDOW_AFTER_MINUTES,
    RELEASE_WINDOW_BEFORE_MINUTES,
    EconomicCalendar,
    parse_ff_calendar,
)
from retrieval.embedder import LocalHashingEmbedder

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ff_calendar_thisweek_2026-10-25.json"
GDP = datetime(2026, 10, 29, 12, 30, tzinfo=UTC)        # Advance GDP q/q, 08:30 EDT in the fixture


def _rows() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _calendar(tmp_path: Path, at: datetime, rows: list[dict] | None = None, fetch=None) -> EconomicCalendar:
    raw = json.dumps(rows if rows is not None else _rows()).encode("utf-8")
    calendar = EconomicCalendar(tmp_path, fetch=fetch or (lambda url: raw), clock=lambda: at.timestamp())
    calendar.refresh(force=True)
    return calendar


def test_real_response_schema_and_timezone() -> None:
    releases, report = parse_ff_calendar(_rows())
    assert report.rows == report.parsed == 96
    assert report.offset_mismatches == 0, "embedded -04:00 agrees with New York (EDT) for this week"
    assert report.usd_clock_agreement == 1.0, "every US high-impact release sits on an agency clock"
    gdp = next(r for r in releases if r.event == "Advance GDP q/q")
    assert gdp.utc == GDP.isoformat() and gdp.currency == "USD" and gdp.impact == "high" and gdp.forecast == "3.2%"
    holiday = next(r for r in releases if r.impact == "holiday")
    assert holiday.time_known is False and holiday.utc is None, "an all-day row's clock time is not a release time"
    assert "actual" not in {k for row in _rows() for k in row}, "the source carries no actuals"


def test_midnight_placeholder_and_stale_offset_are_handled() -> None:
    rows = [
        {"title": "X", "country": "EUR", "date": "2026-11-03T00:00:00-05:00", "impact": "High", "forecast": "", "previous": ""},
        # After the US DST switch (1 Nov 2026) New York is -05:00; a stale -04:00 would be an hour early.
        {"title": "CPI m/m", "country": "USD", "date": "2026-11-03T08:30:00-04:00", "impact": "High", "forecast": "", "previous": ""},
    ]
    releases, report = parse_ff_calendar(rows)
    by_title = {r.event: r for r in releases}
    assert by_title["X"].time_known is False
    assert by_title["CPI m/m"].utc == datetime(2026, 11, 3, 13, 30, tzinfo=UTC).isoformat()
    assert report.offset_mismatches == 1 and report.time_unknown == 1


@pytest.mark.parametrize("minutes_before, phase", [(10, "pre_release"), (-5, "post_release")])
def test_release_proximity_inside_the_window(tmp_path: Path, minutes_before: float, phase: str) -> None:
    calendar = _calendar(tmp_path, GDP - timedelta(minutes=minutes_before))
    proximity = calendar.proximity(["EUR", "USD"])
    assert proximity["in_window"]["phase"] == phase
    assert proximity["in_window"]["event"] in {"Advance GDP q/q", "Core PCE Price Index m/m"}


def test_no_window_far_from_a_release_and_currency_filter(tmp_path: Path) -> None:
    calendar = _calendar(tmp_path, GDP - timedelta(hours=30))
    proximity = calendar.proximity(["EUR", "USD"])
    assert proximity["in_window"] is None and proximity["minutes_to_next"] is not None
    assert calendar.proximity(["XXX"])["minutes_to_next"] is None
    assert RELEASE_WINDOW_BEFORE_MINUTES == 15.0 and RELEASE_WINDOW_AFTER_MINUTES == 10.0


def test_fetch_budget_stale_cache_and_atomic_write(tmp_path: Path) -> None:
    calls = {"n": 0}
    raw = FIXTURE.read_bytes()

    def fetch(url):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("429 Too Many Requests")
        return raw

    now = {"t": GDP.timestamp()}
    calendar = EconomicCalendar(tmp_path, fetch=fetch, clock=lambda: now["t"])
    assert calendar.refresh() is True and calls["n"] == 1
    assert calendar.refresh() is False and calls["n"] == 1, "at most one fetch per hour"
    now["t"] += MIN_FETCH_INTERVAL_SECONDS + 1
    assert calendar.refresh() is False and calls["n"] == 2
    status = calendar.status()
    assert "429" in status["last_error"] and status["rows"] == 96 and status["age_seconds"] > MIN_FETCH_INTERVAL_SECONDS
    assert (tmp_path / CACHE_FILE).exists() and not list(tmp_path.glob("*.tmp"))
    reloaded = EconomicCalendar(tmp_path, fetch=lambda url: (_ for _ in ()).throw(AssertionError("no fetch")),
                                clock=lambda: now["t"])
    assert reloaded.status()["rows"] == 96, "a restart serves the cached file without the network"


def test_non_list_response_is_rejected(tmp_path: Path) -> None:
    calendar = EconomicCalendar(tmp_path, fetch=lambda url: b'{"error": "rate limited"}', clock=lambda: 0.0)
    assert calendar.refresh(force=True) is False and calendar.status()["state"] == "unavailable"
    assert not (tmp_path / CACHE_FILE).exists()


def test_tracker_sets_release_proximity_only_inside_the_window(tmp_path: Path) -> None:
    at = GDP - timedelta(minutes=5)
    calendar = _calendar(tmp_path, at)
    tracker = CurrencyBiasTracker(LocalHashingEmbedder(16).embed, clock=lambda: at, calendar=calendar)
    snap = tracker.snapshot()
    risk = snap["pairs"]["EUR/USD"]["event_risk"]
    assert risk["release_proximity"]["phase"] == "pre_release" and risk["calendar"] == "live"
    assert risk["next_release"]["minutes"] == pytest.approx(5.0)
    assert snap["calendar"]["rows"] == 96
    assert snap["pairs"]["USD/JPY"]["event_risk"]["release_proximity"] is not None, "USD leg"
    far = CurrencyBiasTracker(LocalHashingEmbedder(16).embed, clock=lambda: GDP - timedelta(days=2),
                              calendar=_calendar(tmp_path / "far", GDP - timedelta(days=2)))
    assert far.snapshot()["pairs"]["EUR/USD"]["event_risk"]["release_proximity"] is None


@pytest.mark.asyncio
async def test_calendar_route(tmp_path: Path) -> None:
    from api.main import build_app
    from tests.api.test_routes import _build_service, _request

    status, payload = await _request(build_app(_build_service()), "GET", "/v1/calendar/upcoming")
    assert status == 501
    calendar = _calendar(tmp_path, GDP - timedelta(hours=1))
    app = build_app(_build_service(), calendar=calendar)
    status, payload = await _request(app, "GET", "/v1/calendar/upcoming?currencies=usd&hours=2")
    assert status == 200 and payload["currencies"] == ["USD"]
    assert {r["event"] for r in payload["releases"]} >= {"Advance GDP q/q"}
    assert all(r["currency"] == "USD" and r["time_known"] for r in payload["releases"])
    status, _ = await _request(app, "GET", "/v1/calendar/upcoming?hours=0")
    assert status == 400
