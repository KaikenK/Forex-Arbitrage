"""Documented release clocks for US statistical releases, used to fill missing calendar times.

Why this exists. The ForexFactory dump has lost the time of day on ~30% of its rows: they are
stamped 00:00:00 local, which the parser originally took as a real release time (2026-10-24,
RESEARCH_TRACKER §6). For 2018-2025 every Non-Farm Payrolls row is affected, and so are all
Retail Sales m/m and almost all CPI m/m rows. Those are the releases the positive control
depends on, and their clock time is not actually unknown: US agencies publish on a fixed
clock (BLS, Census and BEA at 08:30 ET, ISM at 10:00 ET, ...). Andersen, Bollerslev, Diebold
& Vega (2003) take release times from these agency schedules in the same way.

Rules, fixed before any result was examined:
  1. An observed timestamp is never overwritten. Only rows whose time is missing are filled.
  2. A family's clock is used only if the dump's own *observed* times confirm it: at least
     `MIN_AGREEMENT` of at least `MIN_TIMED_ROWS` timed rows fall on that clock. A family
     that fails stays date-only, so it is excluded from intraday analysis (fail closed).
  3. Imputed rows are labelled `timestamp_source="schedule"`, so every estimate can be
     re-run on observed times alone as a robustness check.

The release date is the row's date in the source's own clock. For US releases between
08:15 and 14:00 ET, that date equals the New York date in any source zone up to +9:45 ahead
of New York (the dump's zone, Asia/Tehran, is +7:30 to +9:30 ahead). `validate_release_clocks` checks this on
the observed rows and counts any date mismatch as a disagreement.

Requires IANA time-zone data (`pip install tzdata` on Windows).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from .calendar import CalendarEvent

MIN_TIMED_ROWS = 20
MIN_AGREEMENT = 0.95


@dataclass(frozen=True)
class ReleaseFamily:
    """Indicators that one agency publishes together, on one documented clock."""
    name: str
    currency: str
    zone: str
    clock: time
    events: frozenset[str]


def _family(name: str, hhmm: str, *events: str, currency: str = "USD",
            zone: str = "America/New_York") -> ReleaseFamily:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return ReleaseFamily(name=name, currency=currency, zone=zone, clock=time(hour, minute),
                         events=frozenset(events))


# Only releases with a single fixed clock. Excluded on purpose: FOMC statements and rate
# decisions (14:15 ET until 2013, 14:00 after, and other times in between), all speeches
# and testimony, UoM sentiment (09:55 vs 10:00 ET), crude inventories (holiday weeks move
# to Thursday 11:00 ET) and the Philly Fed index (its clock changed during the sample).
RELEASE_FAMILIES: tuple[ReleaseFamily, ...] = (
    _family("BLS Employment Situation", "08:30",
            "Non-Farm Employment Change", "Unemployment Rate", "Average Hourly Earnings m/m"),
    _family("BLS CPI", "08:30", "CPI m/m", "Core CPI m/m", "CPI y/y"),
    _family("BLS PPI", "08:30", "PPI m/m", "Core PPI m/m"),
    _family("DOL Weekly Claims", "08:30", "Unemployment Claims"),
    _family("Census Retail Sales", "08:30", "Retail Sales m/m", "Core Retail Sales m/m"),
    _family("Census Durable Goods", "08:30", "Durable Goods Orders m/m", "Core Durable Goods Orders m/m"),
    _family("Census Residential Construction", "08:30", "Building Permits", "Housing Starts"),
    _family("BEA GDP", "08:30", "Advance GDP q/q", "Prelim GDP q/q", "Final GDP q/q"),
    _family("BEA Personal Income and Outlays", "08:30", "Core PCE Price Index m/m"),
    _family("Census Trade", "08:30", "Trade Balance"),
    _family("ADP Employment Report", "08:15", "ADP Non-Farm Employment Change"),
    _family("ISM Manufacturing", "10:00", "ISM Manufacturing PMI"),
    _family("ISM Services", "10:00", "ISM Services PMI"),
    _family("Conference Board Confidence", "10:00", "CB Consumer Confidence"),
    _family("BLS JOLTS", "10:00", "JOLTS Job Openings"),
    _family("Census New Home Sales", "10:00", "New Home Sales"),
    _family("NAR Existing Home Sales", "10:00", "Existing Home Sales"),
    _family("NAR Pending Home Sales", "10:00", "Pending Home Sales m/m"),
    _family("FOMC Minutes", "14:00", "FOMC Meeting Minutes"),
)

_FAMILY_BY_EVENT: dict[tuple[str, str], ReleaseFamily] = {
    (family.currency, event): family for family in RELEASE_FAMILIES for event in family.events
}


def family_for(currency: str, event_name: str) -> ReleaseFamily | None:
    return _FAMILY_BY_EVENT.get((currency, event_name))


def scheduled_timestamp(family: ReleaseFamily, local_date) -> datetime:
    """The UTC instant of `family`'s clock on `local_date`, DST-aware via tzdata."""
    from datetime import UTC
    return datetime.combine(local_date, family.clock, tzinfo=ZoneInfo(family.zone)).astimezone(UTC)


@dataclass(frozen=True)
class FamilyCheck:
    family: str
    clock: str
    timed_rows: int
    agreeing_rows: int
    missing_rows: int

    @property
    def agreement(self) -> float:
        return self.agreeing_rows / self.timed_rows if self.timed_rows else 0.0

    @property
    def validated(self) -> bool:
        return self.timed_rows >= MIN_TIMED_ROWS and self.agreement >= MIN_AGREEMENT


def validate_release_clocks(events: list[CalendarEvent]) -> dict[str, FamilyCheck]:
    """For each family, how often the dump's observed times agree with the documented clock."""
    timed: dict[str, int] = {family.name: 0 for family in RELEASE_FAMILIES}
    agreeing = dict(timed)
    missing = dict(timed)
    for event in events:
        family = family_for(event.currency, event.event_name)
        if family is None:
            continue
        if not event.has_exact_time:
            missing[family.name] += 1
            continue
        timed[family.name] += 1
        if event.source_local_date is not None and \
                event.timestamp == scheduled_timestamp(family, event.source_local_date):
            agreeing[family.name] += 1
    return {
        family.name: FamilyCheck(family=family.name, clock=family.clock.strftime("%H:%M"),
                                 timed_rows=timed[family.name], agreeing_rows=agreeing[family.name],
                                 missing_rows=missing[family.name])
        for family in RELEASE_FAMILIES
    }


def impute_release_times(
    events: list[CalendarEvent],
    checks: dict[str, FamilyCheck] | None = None,
) -> list[CalendarEvent]:
    """Fill missing times for validated families. Observed times are left untouched."""
    checks = checks if checks is not None else validate_release_clocks(events)
    filled: list[CalendarEvent] = []
    for event in events:
        family = family_for(event.currency, event.event_name)
        if (event.has_exact_time or family is None or event.source_local_date is None
                or not checks[family.name].validated):
            filled.append(event)
            continue
        filled.append(replace(
            event,
            timestamp=scheduled_timestamp(family, event.source_local_date),
            timestamp_precision_minutes=0.0,
            timestamp_source="schedule",
        ))
    return filled
