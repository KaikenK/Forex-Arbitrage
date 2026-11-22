"""Tiny seeded checks for research/run_synthetic_detection_eval.py (offline, a few seconds)."""
from __future__ import annotations

import json

import pytest

from research import run_synthetic_detection_eval as ev


def _run(inj, hits):
    return {"seed": 0, "injections": inj, "hits": hits}


def _inj(src, t, sym="USDINR"):
    return {"symbol": sym, "source": src, "t_ms": t}


def _hit(srcs, w0, w1, typ="cross_source", sym="USDINR"):
    return {"symbol": sym, "sources": srcs, "type": typ, "w0": w0, "w1": w1}


def test_score_definition_on_handmade_run():
    run = _run([_inj("a", 1000), _inj("b", 5000), _inj("c", 9000, sym="EURINR")],
               [_hit(["a", "x"], 900, 1100),          # matches inj a, latency 100
                _hit(["b", "x"], 5300, 5500),         # 300 ms after inj b: only matches for W >= 300
                _hit(["z", "x"], 2000, 2200)])        # matches nothing
    s0 = ev.score(run, 0, "all")
    assert s0["inj_detected"] == [True, False, False]
    assert s0["hit_true"] == [True, False, False]
    assert s0["latencies"] == [100]
    s = ev.score(run, 300, "all")
    assert s["inj_detected"] == [True, True, False]
    assert s["latencies"] == [100, 500]
    r = ev._rates(s)
    assert r["recall"] == pytest.approx(2 / 3) and r["precision"] == pytest.approx(2 / 3)
    assert ev.score(run, 300, "latency_arbitrage")["hit_true"] == []   # view filters by hit type


def test_small_seeded_run_is_deterministic_and_well_formed(tmp_path):
    out = tmp_path / "o.json"
    args = ["--seeds", "1", "--duration-s", "30", "--W", "0", "100", "--bootstrap", "50", "--out", str(out)]
    a = ev.main(args)
    b = ev.main(args)
    assert a["results"] == b["results"]
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["parameters"]["seeds"] == [1] and d["parameters"]["primary_W_ms"] == 100
    assert len(d["code_commit"]) >= 7
    tot = d["run_totals"][0]
    assert tot["n_ticks"] == 6 * 600            # 30 s / 50 ms steps, 6 sources
    r = d["results"]["all|W=100"]["pooled"]
    assert r["n_injections"] == tot["n_injections"] > 0
    assert 0 <= r["recall"] <= 1 and r["recall_ci95"][0] <= r["recall_ci95"][1]
    # larger tolerance can only add matches
    assert d["results"]["all|W=100"]["pooled"]["n_detected"] >= d["results"]["all|W=0"]["pooled"]["n_detected"]
