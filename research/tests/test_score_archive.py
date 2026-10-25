"""Archive scoring job: resumable chunks and the archive-hash guard (lexicon stage, no torch)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from research.score_archive import _archive_hash, _check_manifest, run_chunked, run_dedup


def _frame(n: int) -> pd.DataFrame:
    return pd.DataFrame({
        "event_id": [f"e{i}" for i in range(n)],
        "timestamp": [datetime(2019, 1, 1, tzinfo=UTC) + timedelta(hours=i) for i in range(n)],
        "headline": ["Euro surges on ECB hike" if i % 2 else "Rupee slumps as outflows rise" for i in range(n)],
        "domain": ["a.com"] * n, "currencies": ["EUR"] * n,
    })


def test_lexicon_stage_resumes_at_the_first_missing_chunk(tmp_path):
    frame = _frame(5)
    _check_manifest(tmp_path, _archive_hash(frame), 2, rebuild=False)
    run_chunked("lexicon", frame, tmp_path, chunk_size=2)
    parts = sorted(tmp_path.glob("part-*.parquet"))
    assert len(parts) == 3 and not list(tmp_path.glob("*.tmp"))
    first_mtime = parts[0].stat().st_mtime_ns
    parts[1].unlink()
    run_chunked("lexicon", frame, tmp_path, chunk_size=2)
    assert parts[1].exists() and parts[0].stat().st_mtime_ns == first_mtime, "done chunks are not redone"
    scores = pd.concat([pd.read_parquet(p) for p in sorted(tmp_path.glob("part-*.parquet"))])
    assert list(scores["event_id"]) == list(frame["event_id"])
    assert (scores["polarity"] > 0).sum() == 2 and (scores["polarity"] < 0).sum() == 3


def test_a_changed_archive_is_never_mixed_with_old_parts(tmp_path):
    _check_manifest(tmp_path, _archive_hash(_frame(4)), 2, rebuild=False)
    run_chunked("lexicon", _frame(4), tmp_path, chunk_size=2)
    with pytest.raises(SystemExit):
        _check_manifest(tmp_path, _archive_hash(_frame(6)), 2, rebuild=False)
    _check_manifest(tmp_path, _archive_hash(_frame(6)), 2, rebuild=True)
    assert not list(tmp_path.glob("part-*"))


def test_dedup_stage_flags_syndicated_copies(tmp_path):
    frame = _frame(4)
    frame.loc[1, "headline"] = frame.loc[0, "headline"] = "Fed holds rates steady"
    run_dedup(frame, tmp_path)
    result = pd.read_parquet(tmp_path / "part-dedup.parquet").set_index("event_id")
    assert result.loc["e0", "canonical"] and not result.loc["e1", "canonical"]
    assert result.loc["e0", "coverage_count"] == 2
