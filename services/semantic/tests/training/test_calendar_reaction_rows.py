"""Calendar-release training rows: leak-safe alignment, USD orientation, views, split.

No research/ import and no price files: releases and bars are synthetic.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from training.calendar_reaction_rows import (
    VIEW_CALENDAR,
    VIEW_LIVE,
    CalendarRelease,
    MinuteBars,
    align_return,
    build_release_rows,
    hours_needed,
    release_headline,
    release_id_for,
    surprise_word_direction,
)
from training.reaction.config import ReactionTrainingConfig
from training.reaction.train import (
    assemble_reaction_features,
    load_reaction_rows,
    release_grouped_time_split,
)

RELEASE = datetime(2024, 1, 5, 13, 30, tzinfo=UTC)


def _release(*, name="Non-Farm Employment Change", z=1.0, sign=1, at=RELEASE) -> CalendarRelease:
    return CalendarRelease(
        release_id=release_id_for("moment", at), timestamp=at, event_name=name, country="United States",
        actual=256_000.0, forecast=160_000.0, previous=212_000.0, standardized_surprise=z, news_sign=sign,
    )


def _bars(start: datetime, prices: dict[int, float]) -> MinuteBars:
    """Bars stamped at close, at `start + minute` for each key."""
    times = [start + timedelta(minutes=m) for m in sorted(prices)]
    return MinuteBars(times, [prices[m] for m in sorted(prices)])


def _price_path(pre: float, post: float) -> dict[int, float]:
    # Minute 0 is the bar that closes at the release instant: the last pre-release price.
    path = {0: pre}
    path.update({m: post for m in range(1, 1450)})
    return path


def test_entry_is_the_last_pre_release_close_and_lags_fail_closed() -> None:
    bars = _bars(RELEASE, {-1: 1.1000, 0: 1.1000, 60: 1.0950})
    entry_time, entry_price, exit_time, exit_price = align_return(bars, RELEASE, 60)
    assert entry_time == RELEASE and entry_price == pytest.approx(1.1000)
    assert exit_time == RELEASE + timedelta(minutes=60) and exit_price == pytest.approx(1.0950)

    stale_entry = _bars(RELEASE, {3: 1.1, 63: 1.2})            # first bar 3 min late
    assert align_return(stale_entry, RELEASE, 60) is None
    weekend_gap = _bars(RELEASE, {0: 1.1, 60 * 60: 1.2})        # exit bar 59 h late
    assert align_return(weekend_gap, RELEASE, 1440) is None


def test_labels_are_the_dollars_return_and_magnitude_is_the_pairs() -> None:
    """A good US print: USD up -> EUR/USD falls, USD/JPY rises; both labels must be > 0."""
    rows = build_release_rows(
        _release(z=2.0),
        {"EUR/USD": _bars(RELEASE, _price_path(1.1000, 1.0989)),
         "USD/JPY": _bars(RELEASE, _price_path(150.00, 150.15)),
         "EUR/JPY": _bars(RELEASE, _price_path(165.0, 165.1))},
        horizons=[60], views=[VIEW_LIVE],
    )
    by_pair = {row["pair"]: row for row in rows}
    assert set(by_pair) == {"EUR/USD", "USD/JPY"}, "a pair without a USD leg has no USD label"
    assert by_pair["EUR/USD"]["reaction_label"] == pytest.approx(-(1.0989 / 1.1000 - 1))
    assert by_pair["EUR/USD"]["reaction_label"] > 0 and by_pair["USD/JPY"]["reaction_label"] > 0
    assert by_pair["EUR/USD"]["magnitude_label"] == pytest.approx(abs(1.0989 / 1.1000 - 1))


def test_views_share_a_release_and_only_the_calendar_view_has_numbers() -> None:
    rows = build_release_rows(_release(z=9.0), {"EUR/USD": _bars(RELEASE, _price_path(1.1, 1.09))},
                              horizons=[60, 240])
    live = [r for r in rows if r["source_metadata"]["row_view"] == VIEW_LIVE]
    calendar = [r for r in rows if r["source_metadata"]["row_view"] == VIEW_CALENDAR]
    assert len(live) == len(calendar) == 2
    assert {r["source_metadata"]["release_id"] for r in rows} == {_release().release_id}
    # One event id per (indicator, pair, view), shared across horizons like the USD/INR rows.
    assert len({r["source_metadata"]["event_id"] for r in rows}) == 2
    for row in live:  # exactly what the live bridge sends for a feed headline
        assert row["scheduled"] is False and row["standardized_surprise"] is None and row["actual"] is None
        assert row["macro_event_type"] is None and row["event_category"] == "macro_data"
        assert row["source_metadata"]["source"] == "RSS"
    for row in calendar:
        assert row["scheduled"] is True and row["actual"] == 256_000.0
        assert row["standardized_surprise"] == 5.0, "winsorized at +5 sigma"
        assert row["macro_event_type"] == "nfp"


def test_headline_wording_follows_the_economic_sign_not_the_raw_surprise() -> None:
    higher_unemployment = _release(name="Unemployment Rate", z=1.5, sign=-1)
    assert surprise_word_direction(release_headline(higher_unemployment)) == -1
    hot_cpi = _release(name="CPI m/m", z=1.5, sign=1)
    assert surprise_word_direction(release_headline(hot_cpi)) == 1
    in_line = _release(z=0.1)
    assert surprise_word_direction(release_headline(in_line)) == 0
    assert not any(ch.isdigit() for ch in release_headline(_release(z=2.0))), "no printed numbers"


def test_hours_needed_cover_entry_and_exit_windows_by_bar_start_hour() -> None:
    hours = hours_needed([_release(at=datetime(2024, 1, 5, 13, 0, tzinfo=UTC))], [60])
    # Entry bars stamped 13:00..13:02 start at 12:59..13:01 -> hours 12 and 13.
    assert datetime(2024, 1, 5, 12, tzinfo=UTC) in hours and datetime(2024, 1, 5, 13, tzinfo=UTC) in hours
    assert datetime(2024, 1, 5, 14, tzinfo=UTC) in hours


def test_release_grouped_split_never_splits_a_release(tmp_path) -> None:
    """Many rows share one timestamp; a row-index cut would put them on both sides."""
    import json

    releases = [_release(at=RELEASE + timedelta(days=d)) for d in range(10)]
    table = tmp_path / "rows.jsonl"
    with table.open("w", encoding="utf-8") as handle:
        for release in releases:
            bars = {pair: _bars(release.timestamp, _price_path(1.0, 1.001))
                    for pair in ("EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF")}
            for row in build_release_rows(release, bars, horizons=[60, 240]):
                handle.write(json.dumps(row) + "\n")
    config = ReactionTrainingConfig(training_table_path=table, output_dir=tmp_path, hash_dimension=32)
    features = [assemble_reaction_features(row, config) for row in load_reaction_rows(config)]
    train, validation, test = release_grouped_time_split(features, 0.7, 0.15)
    ids = [{row.source_metadata["release_id"] for row in part} for part in (train, validation, test)]
    assert not (ids[0] & ids[1] or ids[0] & ids[2] or ids[1] & ids[2])
    assert len(train) + len(validation) + len(test) == len(features) == 10 * 4 * 2 * 2
    assert max(row.timestamp for row in train) < min(row.timestamp for row in test)
