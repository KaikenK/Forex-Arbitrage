"""research/reproduce.py: step order and prerequisite gates. Nothing is executed."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from research import reproduce as rp
from research.data.histdata import zip_path

BEFORE = rp.GDELT_CUTOFF_UTC - timedelta(hours=1)
AFTER_ALL = rp.H6B_WINDOW_END_UTC + timedelta(hours=1)
STEP = {s.name: s for s in rp.STEPS}


def _ctx(tmp_path: Path, now: datetime, *, histdata: bool = True) -> rp.Context:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(exist_ok=True)
    hist = tmp_path / "histdata"
    if histdata:
        for pair in rp.MAJORS:
            for year in rp.HISTDATA_YEARS:
                path = zip_path(hist, pair, year)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"PK")
    return rp.Context(now=now, artifacts=artifacts, histdata_dir=hist, recordings_dir=tmp_path)


def _freeze(ctx: rp.Context, sha: str = "abc") -> None:
    ctx.freeze_path.write_text(json.dumps({"sha256": sha}), encoding="utf-8")


def _score(ctx: rp.Context, stage: str, sha: str = "abc") -> None:
    path = ctx.scores_dir / stage / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"freeze_sha256": sha}), encoding="utf-8")


def test_every_step_names_a_real_module_and_the_order_is_the_protocol_order() -> None:
    root = Path(rp.__file__).resolve().parent
    for step in rp.STEPS:
        module = step.args[0].split(".", 1)[1]
        assert (root / f"{module}.py").exists(), step.name
    order = [s.name for s in rp.STEPS]
    assert order.index("calendar") < order.index("positive_control") < order.index("h2h3")
    assert order.index("freeze") < order.index("score_dedup") < order.index("score_finbert") < order.index("h1_primary")
    assert order.index("h1_primary") < order.index("h1_decide") and order[-1] == "h6b"


def test_list_works_now_against_the_real_tree() -> None:
    lines = rp.list_steps(rp.Context())
    assert len(lines) == len(rp.STEPS) and all("python -m research." in line for line in lines)


def test_freeze_waits_for_the_cutoff(tmp_path: Path) -> None:
    assert "cutoff not reached" in rp.status(STEP["freeze"], _ctx(tmp_path, BEFORE))
    assert rp.status(STEP["freeze"], _ctx(tmp_path, rp.GDELT_CUTOFF_UTC)) is None


def test_scoring_needs_the_freeze_and_matching_hashes(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, AFTER_ALL)
    assert "not frozen" in rp.status(STEP["score_dedup"], ctx)
    _freeze(ctx)
    assert rp.status(STEP["score_dedup"], ctx) is None
    assert "'dedup' not run" in rp.status(STEP["score_finbert"], ctx)
    _score(ctx, "dedup", sha="old")
    assert "not produced on the current freeze" in rp.status(STEP["score_finbert"], ctx)
    _score(ctx, "dedup")
    assert rp.status(STEP["score_finbert"], ctx) is None


def test_h1_primary_needs_embeddings_but_robustness_runs_do_not(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, AFTER_ALL)
    (ctx.artifacts / "calendar_events.parquet").write_bytes(b"")
    _freeze(ctx)
    for stage in ("dedup", "finbert"):
        _score(ctx, stage)
    assert "'embed'" in rp.status(STEP["h1_primary"], ctx)
    assert rp.status(STEP["h1_tfidf"], ctx) is None
    _score(ctx, "embed")
    assert rp.status(STEP["h1_primary"], ctx) is None and rp.status(STEP["h6"], ctx) is None


def test_intraday_steps_need_histdata(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, AFTER_ALL, histdata=False)
    (ctx.artifacts / "calendar_events.parquet").write_bytes(b"")
    assert "HistData M1 not cached" in rp.status(STEP["h2h3"], ctx)
    assert rp.status(STEP["r1_usdinr"], ctx) is None, "daily R1 needs no minute bars"


def test_h6b_waits_for_its_window_to_close(tmp_path: Path) -> None:
    assert "window open" in rp.status(STEP["h6b"], _ctx(tmp_path, rp.H6B_WINDOW_END_UTC - timedelta(seconds=1)))
    assert rp.status(STEP["h6b"], _ctx(tmp_path, rp.H6B_WINDOW_END_UTC)) is None


def test_run_refuses_blocked_steps_and_runs_ready_ones(tmp_path: Path) -> None:
    calls = []

    class Done:
        returncode = 0

    def runner(cmd, cwd):
        calls.append(cmd)
        return Done()

    assert rp.run_step(STEP["freeze"], _ctx(tmp_path, BEFORE), runner=runner) == 2 and calls == []
    assert rp.run_step(STEP["freeze"], _ctx(tmp_path, AFTER_ALL), runner=runner) == 0
    assert calls[0][1:] == ["-m", "research.freeze_gdelt_sample"]


def test_h6b_script_refuses_before_the_window_and_drops_late_rows(monkeypatch) -> None:
    import research.run_h6b as h6b

    late = {"timestamp_ms": int(h6b.WINDOW_END_UTC.timestamp() * 1000), "semantic_snapshot": {}}
    rows, skipped = h6b.evaluation_rows([late], {}, one_sided="drop")
    assert rows == [] and skipped == {"after_window_end": 1}
    assert h6b.WINDOW_END_UTC == rp.H6B_WINDOW_END_UTC
    monkeypatch.setattr("sys.argv", ["run_h6b", "--recordings", "."])
    if datetime.now(UTC) < h6b.WINDOW_END_UTC:
        with pytest.raises(SystemExit, match="window is still open"):
            h6b.main()
