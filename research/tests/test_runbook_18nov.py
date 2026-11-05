"""Dry run of the 18 Nov sequence on synthetic data only: gates, step arguments, and every script
running end to end (no network, no real recordings or prediction log)."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from research import reproduce as rp
from research import run_c40 as c40
from research import run_h6b as h6b
from research import score_live_predictions as live
from research.semantic.currency_bias import H6B_UNRESOLVED
from research.tests.test_c40 import AFTER, synthetic_recordings
from research.tests.test_live_scripts import T0, _row

LIVE_STEPS = ("live_scoring_v1", "live_scoring_release_v20", "live_scoring_release_v21", "rq5_extension", "c40")


def test_18_nov_steps_are_blocked_before_and_open_at_midnight_ist(tmp_path):
    steps = {s.name: s for s in rp.STEPS}
    before = rp.Context(now=rp.LIVE_SCORING_FROM_UTC - timedelta(seconds=1), artifacts=tmp_path)
    at = rp.Context(now=rp.LIVE_SCORING_FROM_UTC, artifacts=tmp_path)
    for name in LIVE_STEPS:
        assert rp.status(steps[name], before), name
        assert rp.status(steps[name], at) is None, name


def test_release_model_split_and_no_row_after_the_sample():
    steps = {s.name: s for s in rp.STEPS}
    v20, v21 = steps["live_scoring_release_v20"].args, steps["live_scoring_release_v21"].args
    assert v20[v20.index("--since") + 1] == rp.V1_DEPLOYMENT_END and v20[v20.index("--until") + 1] == rp.C38_DEPLOYED
    assert v21[v21.index("--since") + 1] == rp.C38_DEPLOYED and v21[v21.index("--until") + 1] == rp.LIVE_SAMPLE_END
    assert datetime.fromisoformat(rp.LIVE_SAMPLE_END) == rp.LIVE_SCORING_FROM_UTC == live.SCORING_OPENS_UTC
    assert datetime.fromisoformat(rp.LIVE_SAMPLE_END) == h6b.RQ5_END_UTC == c40.END_UTC


def _bars(pair, start, end):
    index = pd.date_range(start.replace(minute=0, second=0, microsecond=0), end, freq="1h", tz=UTC)
    return pd.Series(1.10 + 0.0001 * np.arange(len(index)), index=index)


def test_score_live_predictions_gate_and_offline_run(tmp_path):
    path = tmp_path / "live_predictions.jsonl"
    rows = [_row(event_id=f"e{i}", headline=f"headline {i}",
                 published_at=(T0 + timedelta(hours=i, minutes=2)).isoformat(),
                 release_direction=1 if i % 2 else -1) for i in range(6)]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = tmp_path / "score.json"
    argv = ["--predictions", str(path), "--basis", "release", "--since", (T0 - timedelta(days=1)).isoformat(),
            "--until", (T0 + timedelta(days=1)).isoformat(), "--out", str(out)]
    with pytest.raises(SystemExit, match="opens"):
        live.main(argv, now=live.SCORING_OPENS_UTC - timedelta(minutes=1), bars=_bars)
    assert not out.exists()
    assert live.main(argv, now=AFTER, bars=_bars) == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["rows_scored"] == 6 and result["accuracy"] == pytest.approx(0.5)


def test_rq5_extension_and_c40_on_one_synthetic_recording(tmp_path, monkeypatch):
    rec = tmp_path / "recordings"
    synthetic_recordings(rec)
    days = sorted(d for d in rec.iterdir() if d.is_dir())
    quotes, opportunities = h6b.load_quotes(days), h6b.load_opportunities(days)
    rows, skipped = h6b.evaluation_rows(opportunities, quotes, window_start=h6b.WINDOW_END_UTC,
                                        window_end=h6b.RQ5_END_UTC,
                                        one_sided=str(H6B_UNRESOLVED["one_sided_next_quote"]))
    section = h6b.evaluate_section(rows, skipped, h6b.WINDOW_END_UTC, h6b.RQ5_END_UTC)
    assert section["rows"] == 24 and skipped == {"before_window_start": 12}
    assert set(section["ranking"]["auc"]) >= {"detector", "plus_risk", "plus_risk_direction", "plus_window"}
    monkeypatch.setattr(c40, "MIN_RELEASE_WINDOWS", 1)
    monkeypatch.setattr(c40, "MIN_OUTCOMES", 10)
    report = c40.run(rec, None, None, now=AFTER, draws=50)
    assert report["releases_high_impact"] == 0
    assert "not_computable" in report["main"]["tests"]["C40-1"]      # no calendar: no release window
