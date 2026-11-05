"""Dashboard research summary: built from committed artifacts only, verdicts carried through."""
from __future__ import annotations

import json

from research import make_ui_summary as ui


def _write(tmp_path, name, data):
    (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")


def test_verdicts_and_numbers_come_from_the_artifacts(tmp_path):
    _write(tmp_path, "h6_results.json", {"verdict": "NOT SUPPORTED", "h6a_primary_daily": {"pair_bias": {
        "criterion_i": {"beta": 0.0952, "p": 0.891}, "criterion_ii": {"mse_full": 2270.48, "mse_baseline": 2268.39,
                                                                     "hit_rate_full": 0.494, "hit_rate_baseline": 0.501}}}})
    _write(tmp_path, "window30_results.json", {"tests": {"T2_activity_60": {"beta": 0.0481, "q_bh": 1.46e-10},
                                                         "T2_activity_30": {"beta": 0.0427, "q_bh": 6.1e-9},
                                                         "T1_direction_30": {"q_bh": 0.95}, "T1_direction_60": {"q_bh": 0.89}}})
    summary = ui.build(tmp_path)
    by_id = {r["id"]: r for r in summary["results"]}
    assert by_id["H6"]["verdict"] == "NOT SUPPORTED" and by_id["H6"]["tone"] == "down"
    assert by_id["H6"]["numbers"][0] == {"label": "pair-bias β", "value": "0.095", "detail": "p 0.891"}
    assert by_id["window30"]["numbers"][0]["value"] == "0.048" and "1.5e-10" in by_id["window30"]["numbers"][0]["detail"]
    assert by_id["H1"]["verdict"] == "MISSING"                      # absent artifact is shown as missing, not invented
    assert {s["id"] for s in summary["sealed"]} == {"RQ5", "C40", "Prospective", "C43", "Latency"}
    assert summary["sealed"][0]["opens"] == "2026-11-17T18:30:00Z"


def test_builds_on_the_committed_artifacts(tmp_path):
    summary = ui.build(ui.ARTIFACTS)
    verdicts = {r["id"]: r["verdict"] for r in summary["results"]}
    assert verdicts["H6"] == "NOT SUPPORTED" and verdicts["H1"] == "NOT SUPPORTED" and verdicts["H5"] == "PARTIAL"
    assert "MISSING" not in verdicts.values()
    out = tmp_path / "s.json"
    ui.main(["--out", str(out)])
    assert json.loads(out.read_text(encoding="utf-8"))["thesis"] == ui.THESIS
