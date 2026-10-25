"""GDELT sample freeze at the locked cutoff (§6, 2026-10-25). No network, synthetic shards."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

import research.freeze_gdelt_sample as freeze_mod
from research.data.base import RawEvent
from research.freeze_gdelt_sample import (
    CUTOFF_UTC,
    EXPECTED_WINDOWS,
    FreezeError,
    build_freeze,
    load_frozen_events,
    verify_freeze,
    write_freeze,
)
from research.gdelt_backfill import _write_window

BEFORE = CUTOFF_UTC - timedelta(hours=1)
AFTER = CUTOFF_UTC + timedelta(minutes=1)


def _event(event_id: str, headline: str = "ECB holds rates as euro steadies on inflation data") -> RawEvent:
    return RawEvent(event_id=event_id, timestamp=datetime(2019, 1, 2, 12, tzinfo=UTC), headline=headline,
                    source="gdelt", currencies=("EUR",))


def _shard(shard_dir, name: str, n_events: int, *, completed_at=None, mtime=None, confirmed_empty=True):
    if n_events:
        pd.DataFrame([_event(f"{name}-{i}").to_row() for i in range(n_events)]).to_parquet(
            shard_dir / f"{name}.parquet", index=False)
    meta = {"count": n_events, "fetched_at": "2026-08-18T17:00:00+00:00"}
    if completed_at is not None:
        meta["completed_at"] = completed_at.isoformat()
    if n_events == 0:
        meta["confirmed_empty"] = confirmed_empty
    sentinel = shard_dir / f"{name}.done"
    sentinel.write_text(json.dumps(meta), encoding="utf-8")
    if mtime is not None:
        os.utime(sentinel, (mtime.timestamp(), mtime.timestamp()))


@pytest.fixture
def shards(tmp_path):
    _shard(tmp_path, "20180101T000000", 3, completed_at=BEFORE)
    _shard(tmp_path, "20180102T000000", 2, completed_at=AFTER)                 # too late
    _shard(tmp_path, "20180103T000000", 4, mtime=BEFORE)                       # old sentinel, mtime
    _shard(tmp_path, "20180104T000000", 0, completed_at=BEFORE)                # confirmed empty
    _shard(tmp_path, "20180105T000000", 0, mtime=BEFORE, confirmed_empty=False)  # unverified empty
    _shard(tmp_path, "20220101T000000", 5, completed_at=BEFORE)                # outside 2018-2021
    _shard(tmp_path, "20211231T000000", 1, mtime=AFTER)                        # old, mtime too late
    return tmp_path


def test_freeze_membership_follows_completion_time_and_sample_range(shards):
    freeze = build_freeze(shards)
    names = [row["window"] for row in freeze["shards"]]
    assert names == ["20180101T000000", "20180103T000000", "20180104T000000", "20180105T000000"]
    assert freeze["excluded_completed_after_cutoff"] == ["20180102T000000", "20211231T000000"]
    assert freeze["completion_time_source_counts"] == {"completed_at": 2, "mtime": 2}
    assert freeze["events"] == 7
    assert freeze["coverage"] == round(4 / EXPECTED_WINDOWS, 4)
    assert freeze["unverified_empty_windows"] == 1
    assert freeze["coverage_excluding_unverified_empty"] == round(3 / EXPECTED_WINDOWS, 4)
    assert "2018-01-02" in freeze["missing_windows"] and len(freeze["missing_windows"]) == EXPECTED_WINDOWS - 4
    assert freeze["mtime_vs_fetched_at_seconds"]["n"] == 2


def test_write_refuses_before_the_cutoff_and_never_overwrites(shards, tmp_path):
    out = tmp_path / "freeze.json"
    with pytest.raises(FreezeError):
        write_freeze(shards, out, now=BEFORE)
    assert not out.exists()
    freeze = write_freeze(shards, out, now=AFTER)
    assert out.exists() and freeze["frozen_at_utc"] == AFTER.isoformat()
    with pytest.raises(FreezeError):
        write_freeze(shards, out, now=AFTER + timedelta(days=1))


def test_hash_detects_any_change_to_a_frozen_shard(shards):
    freeze = build_freeze(shards)
    verify_freeze(freeze, shards)
    _shard(shards, "20180101T000000", 4, completed_at=BEFORE)       # rewritten after freezing
    with pytest.raises(FreezeError):
        verify_freeze(freeze, shards)


def test_frozen_events_come_only_from_listed_shards(shards):
    freeze = build_freeze(shards)
    events = load_frozen_events(freeze, shards)
    windows = {event_id.rsplit("-", 1)[0] for event_id in events["event_id"]}
    assert windows == {"20180101T000000", "20180103T000000"}
    assert len(events) == 7


def test_later_downloads_do_not_enter_an_existing_freeze(shards):
    freeze = build_freeze(shards)
    _shard(shards, "20180106T000000", 9, completed_at=AFTER)
    assert len(load_frozen_events(freeze, shards)) == 7


def test_backfill_sentinels_now_record_completed_at(tmp_path):
    _write_window(tmp_path, "20190101T000000", [_event("x")])
    meta = json.loads((tmp_path / "20190101T000000.done").read_text(encoding="utf-8"))
    completed = datetime.fromisoformat(meta["completed_at"])
    assert completed.tzinfo is not None and completed >= datetime.fromisoformat(meta["fetched_at"])


def test_h1_h5_and_scores_refuse_without_a_matching_freeze(tmp_path, monkeypatch):
    from research.score_archive import _check_manifest, load_archive, require_frozen_scores

    with pytest.raises(FreezeError):
        freeze_mod.load_freeze(tmp_path / "absent.json")
    stage_dir = tmp_path / "finbert"
    _check_manifest(stage_dir, "abc", 2000, rebuild=False, freeze_sha=None)
    with pytest.raises(SystemExit):
        require_frozen_scores("finbert", "deadbeef", tmp_path)
    _check_manifest(stage_dir, "abc", 2000, rebuild=True, freeze_sha="deadbeef")
    require_frozen_scores("finbert", "deadbeef", tmp_path)

    frozen = pd.DataFrame([_event("frozen-1").to_row()])
    monkeypatch.setattr(freeze_mod, "frozen_events_or_none", lambda: (frozen, {"sha256": "deadbeef"}))
    archive, sha = load_archive(tmp_path / "unused.parquet")
    assert sha == "deadbeef" and list(archive["event_id"]) == ["frozen-1"]
