"""Build the dashboard's research summary from committed result artifacts (no live data).

Reads research/artifacts/{h1,h5,h6,window30,rq2_e2e,h6b,positive_control}_results JSONs and writes
arbex-web/src/data/research_summary.json: per result a verdict, its key numbers and the decision
rule, plus the live tests that are sealed until they open. Nothing here reads the recorder or the
prediction log, so it can run at any time; run it again after a result changes.

  python -m research.make_ui_summary [--artifacts DIR] [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "research" / "artifacts"
OUT = ROOT / "arbex-web" / "src" / "data" / "research_summary.json"
SEALED_UNTIL = "2026-11-17T18:30:00Z"         # 2026-11-18 00:00 IST
LATENCY_OPENS = "2026-11-13T18:30:00Z"        # 2026-11-14 00:00 IST
THESIS = "News tells the engine when to be careful, not which way."


def _load(artifacts: Path, name: str) -> dict | None:
    path = artifacts / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _get(d: Any, *path, default=None):
    for key in path:
        if not isinstance(d, dict) or key not in d:
            return default
        d = d[key]
    return d


def _p(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1e}" if value < 0.001 else f"{value:.3f}"


def _num(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{100 * value:.1f}%"


def _ci_pct(ci) -> str:
    return "—" if not ci else f"[{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]"


VERDICT_TONE = {"SUPPORTED": "up", "PARTIAL": "risk", "NOT SUPPORTED": "down", "PASS": "up", "FAIL": "down",
                "EXPLORATORY": "info", "MISSING": "muted"}


def _entry(id_: str, title: str, verdict: str, numbers: list[dict], rule: str, note: str = "",
           source: str = "") -> dict:
    return {"id": id_, "title": title, "verdict": verdict, "tone": VERDICT_TONE.get(verdict, "muted"),
            "numbers": numbers, "rule": rule, "note": note, "source": source}


def _missing(id_: str, title: str, source: str) -> dict:
    return _entry(id_, title, "MISSING", [], "", f"{source} not found", source)


def h1(a: dict | None) -> dict:
    if a is None:
        return _missing("H1", "Novel headlines move FX more", "h1_results.json")
    numbers = []
    for name, label in (("sentence_transformer", "sentence-transformer"), ("tfidf_svd", "TF-IDF"), ("hashing", "hashing")):
        base = _get(a, name, "majors", "finbert_baseline", default={})
        numbers.append({"label": f"novelty β ({label})", "value": _num(base.get("novelty_beta")),
                        "detail": f"p {_p(base.get('novelty_p'))}"})
    st = _get(a, "sentence_transformer", "majors", "finbert_baseline", default={})
    numbers.append({"label": "out-of-sample MSE", "value": _num(st.get("oos_mse_novelty")),
                    "detail": f"vs {_num(st.get('oos_mse_baseline'))} without novelty"})
    supported = all(_get(a, n, "majors", "finbert_baseline", "verdict") == "supported" for n in a)
    return _entry("H1", "Novel headlines move FX more", "SUPPORTED" if supported else "NOT SUPPORTED", numbers,
                  "Novelty β > 0 at p < 0.05 and lower out-of-sample error, on the majors.",
                  "The strongest embedder gives the wrong sign.", "h1_results.json")


def h5(a: dict | None) -> dict:
    if a is None:
        return _missing("H5", "News carries a market-wide component", "h5_results.json")
    lex, fin = _get(a, "lexicon", "primary", default={}), _get(a, "finbert", "primary", default={})
    verdict = "PARTIAL" if lex.get("verdict") == "partial" or fin.get("verdict") == "partial" else (
        "SUPPORTED" if lex.get("verdict") == "supported" else "NOT SUPPORTED")
    return _entry("H5", "News carries a market-wide component", verdict, [
        {"label": "lexicon (a): implied β", "value": _num(_get(lex, "a", "implied_beta"), 1),
         "detail": f"p {_p(_get(lex, 'a', 'implied_p'))} · passes" if lex.get("a_passed") else "fails"},
        {"label": "lexicon (b)", "value": "fails" if not lex.get("b_passed") else "passes", "detail": "risk proxies"},
        {"label": "FinBERT", "value": fin.get("verdict", "—"), "detail": "neither part passes" if fin.get("verdict") == "neither" else ""},
    ], "(a) the implied market term predicts returns out of sample; (b) it moves with risk proxies.",
        "Partial on the lexicon backend only.", "h5_results.json")


def h6(a: dict | None) -> dict:
    if a is None:
        return _missing("H6", "Currency news bias forecasts pair returns", "h6_results.json")
    pb = _get(a, "h6a_primary_daily", "pair_bias", default={})
    ci, cii = pb.get("criterion_i", {}), pb.get("criterion_ii", {})
    return _entry("H6", "Currency news bias forecasts pair returns", str(a.get("verdict", "—")).upper(), [
        {"label": "pair-bias β", "value": _num(ci.get("beta")), "detail": f"p {_p(ci.get('p'))}"},
        {"label": "out-of-sample MSE", "value": _num(cii.get("mse_full"), 1), "detail": f"vs {_num(cii.get('mse_baseline'), 1)} baseline"},
        {"label": "hit rate", "value": _pct(cii.get("hit_rate_full")), "detail": f"vs {_pct(cii.get('hit_rate_baseline'))} baseline"},
    ], "Daily pair bias: β > 0 at p < 0.05 and lower walk-forward error than the baseline.",
        "No direction from news on held-out months; the intraday secondaries carry no decision weight.",
        "h6_results.json")


def window30(a: dict | None) -> dict:
    if a is None:
        return _missing("window30", "The 30-minute news window", "window30_results.json")
    t = a.get("tests", {})
    return _entry("window30", "The 30-minute news window", "EXPLORATORY", [
        {"label": "activity → move size (60 min)", "value": _num(_get(t, "T2_activity_60", "beta")),
         "detail": f"q {_p(_get(t, 'T2_activity_60', 'q_bh'))}"},
        {"label": "activity → move size (30 min)", "value": _num(_get(t, "T2_activity_30", "beta")),
         "detail": f"q {_p(_get(t, 'T2_activity_30', 'q_bh'))}"},
        {"label": "direction (30 / 60 min)", "value": "null",
         "detail": f"q {_p(_get(t, 'T1_direction_30', 'q_bh'))} / {_p(_get(t, 'T1_direction_60', 'q_bh'))}"},
    ], "Exploratory, BH-corrected: direction, activity and caution over the next 30 and 60 minutes.",
        "News activity predicts how much a pair moves, not which way: the basis for news as risk.",
        "window30_results.json")


def rq2_e2e(a: dict | None) -> dict:
    if a is None:
        return _missing("RQ2-E2E", "Direction from real release headlines", "rq2_e2e.json")
    d = _get(a, "direction", "article_time_r60", default={})
    numbers = [{"label": label, "value": _pct(_get(d, key, "accuracy")), "detail": f"95% CI {_ci_pct(_get(d, key, 'ci95'))}"}
               for key, label in (("structured", "structured extractor"), ("finbert", "FinBERT"),
                                  ("v1_text_model", "V1 text model"))]
    return _entry("RQ2-E2E", "Direction from real release headlines", "NOT SUPPORTED", numbers,
                  "Direction accuracy over the 60 minutes after the article, against 50%.",
                  "Every method's interval includes a coin flip.", "rq2_e2e.json")


def h6b(a: dict | None) -> dict:
    if a is None:
        return _missing("H6b", "Semantic channels improve live ranking", "h6b_results.json")
    r = _get(a, "clean_window", "ranking", default={})
    auc, ci = r.get("auc", {}), r.get("ci95", {})
    diff = ci.get("plus_risk_minus_detector")
    return _entry("H6b", "Semantic channels improve live ranking", "EXPLORATORY", [
        {"label": "still-crossed AUC, detector", "value": _num(auc.get("detector")), "detail": f"{_get(a, 'clean_window', 'rows')} crosses"},
        {"label": "+ risk channel", "value": _num(auc.get("plus_risk")),
         "detail": f"difference CI [{_num(diff[0])}, {_num(diff[1])}]" if diff else ""},
        {"label": "+ risk + direction", "value": _num(auc.get("plus_risk_direction")), "detail": ""},
    ], "Prospective, descriptive: AUC for a cross still being there at the next poll.",
        "The shipped risk channel did not rank persistence better; C40 asks a different question (worse crosses near releases).",
        "h6b_results.json")


def positive_control(a: dict | None) -> dict:
    if a is None:
        return _missing("M3", "Positive control (NFP)", "positive_control.json")
    gate = str(a.get("gate_record", ""))
    after = _get(a, "gate_consistency_check_option_a", "PASS")
    return _entry("M3", "Positive control (NFP)", "FAIL" if gate.startswith("FAIL") else "PASS", [
        {"label": "pre-registered gate", "value": gate.split(":")[0] if gate else "—", "detail": "runs 1-2, clock defects"},
        {"label": "on corrected clocks", "value": "pass" if after else "fail", "detail": "post hoc, all three criteria"},
    ], "NFP surprise moves USD pairs the known way; placebo does not.",
        "Recorded as a failure under the two-attempt rule; both failures traced to timing defects.",
        "positive_control.json")


SEALED = [
    {"id": "RQ5", "title": "RQ5 extension: ranking value on the live recorder", "opens": SEALED_UNTIL},
    {"id": "C40", "title": "C40: worse crosses near releases, stale quotes and news bursts", "opens": SEALED_UNTIL},
    {"id": "Prospective", "title": "Prospective test of the deployed models (V1, V2.0, V2.1)", "opens": SEALED_UNTIL},
    {"id": "C43", "title": "C43: shadow-adjusted vs detector confidence", "opens": SEALED_UNTIL},
    {"id": "Latency", "title": "Arrival latency of live news, and the RQ2 re-test", "opens": LATENCY_OPENS},
]


def _commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def build(artifacts: Path) -> dict:
    results = [
        h1(_load(artifacts, "h1_results.json")),
        h5(_load(artifacts, "h5_results.json")),
        h6(_load(artifacts, "h6_results.json")),
        window30(_load(artifacts, "window30_results.json")),
        rq2_e2e(_load(artifacts, "rq2_e2e.json")),
        h6b(_load(artifacts, "h6b_results.json")),
        positive_control(_load(artifacts, "positive_control.json")),
    ]
    return {"thesis": THESIS, "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "source_commit": _commit(), "results": results, "sealed": SEALED,
            "evidence": [
                {"label": "Direction from news", "value": next(r["verdict"] for r in results if r["id"] == "H6"), "tone": "down"},
                {"label": "News activity → move size", "value": next((n["detail"] for r in results if r["id"] == "window30"
                                                                      for n in r["numbers"][:1]), "—"), "tone": "up"},
                {"label": "Headline direction vs chance", "value": "at chance", "tone": "down"},
            ]}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts", type=Path, default=ARTIFACTS)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    summary = build(args.artifacts)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({len(summary['results'])} results, {len(summary['sealed'])} sealed)")


if __name__ == "__main__":
    main()
