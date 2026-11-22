"""p-value and q-value ledger for the IEEE paper, built from the committed JSON artifacts.

Every p reported anywhere in the paper appears here with the family it was corrected in. q values
are the Benjamini-Hochberg values the analysis scripts already wrote into the artifacts; this file
does not recompute them. Families as registered:
  RQ2 direction   nine windows (non-primary windows carry a BH q in the artifact; the two primary
                  windows were decided on their own p, as registered)
  window30        six tests (T1-T3 at 30 and 60 min), BH over six
  C45             two horizons (T2-clock at 30 and 60 min), BH over two
  C40             C40-1..3, BH over three (C40-1 not computable, so only C40-2 and C40-3 carry a q)
  H1, H5, H6      decisions by pre-set rule; no multiplicity correction was registered
  prospective     descriptive scores of deployed models; p against a coin, not a test

    python -m research.paper.ieee.make_pvalue_ledger        # writes tables/pledger.tex
"""
from __future__ import annotations

import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE.parents[1] / "artifacts"
OUTPUT = HERE / "tables" / "pledger.tex"

RQ2_TARGETS = [("release_1m", "release 0--1"), ("release_5m", "release 0--5"),
               ("release_15m", "release 0--15 (primary)"), ("release_30m", "release 0--30"),
               ("release_60m", "release 0--60"), ("release_120m", "release 0--120"),
               ("arrival_15+15m", "arrival 15--30"), ("arrival_15+45m", "arrival 15--60 (primary)"),
               ("arrival_15+105m", "arrival 15--120")]


def _load(artifacts: Path, name: str) -> dict:
    return json.loads((artifacts / name).read_text(encoding="utf-8"))


def _row(family: str, test: str, p, q=None, note: str = "") -> dict:
    return {"family": family, "test": test, "p": None if p is None else float(p),
            "q": None if q is None else float(q), "note": note}


def build_rows(artifacts: Path = ARTIFACTS) -> list[dict]:
    rows: list[dict] = []
    react = _load(artifacts, "reaction_eval.json")["targets"]
    for key, label in RQ2_TARGETS:
        model = react[key]["direction"]["model"]
        rows.append(_row("RQ2", f"direction vs coin, {label}", model["p_vs_coin"], model.get("q_bh_vs_coin")))

    w30 = _load(artifacts, "window30_results.json")["tests"]
    names = {"T1_direction": "direction (rank IC)", "T2_activity": "activity (log abnormal move)",
             "T3_caution": "caution (rank IC)"}
    for kind, label in names.items():
        for h in (30, 60):
            t = w30[f"{kind}_{h}"]
            rows.append(_row("window30", f"{label}, {h} min", t["p"], t["q_bh"]))

    c45 = _load(artifacts, "c45_results.json")["T2_clock"]
    for h in (30, 60):
        t = c45[f"h{h}"]
        rows.append(_row("C45", f"activity (T2-clock), {h} min", t["p"], t["q_bh"]))

    c40 = _load(artifacts, "c40_results.json")["main"]["tests"]
    rows.append(_row("C40", "C40-1 release windows vs controls", None, None, "not computable"))
    for key, label in (("C40-2", "C40-2 quote age (feed check)"), ("C40-3", "C40-3 activity factor")):
        rows.append(_row("C40", label, c40[key]["p_one_sided"], c40[key]["q_bh"]))

    h1 = _load(artifacts, "h1_results.json")["sentence_transformer"]["majors"]["finbert_baseline"]
    rows.append(_row("H1", "novelty coefficient, majors (wrong sign)", h1["novelty_p"]))

    h5 = _load(artifacts, "h5_results.json")
    for backend in ("lexicon", "finbert"):
        prim = h5[backend]["primary"]
        rows.append(_row("H5", f"{backend}: implied-surprise beta (a)", prim["a"]["implied_p"]))
        rows.append(_row("H5", f"{backend}: loss difference (a)", prim["a"]["loss_diff_p"]))
        for key, label in (("vix_change", "VIX change"), ("ret_USD/JPY", "USD/JPY"), ("ret_USD/CHF", "USD/CHF")):
            rows.append(_row("H5", f"{backend}: market component vs {label} (b)", prim["b"][key]["p"]))

    h6 = _load(artifacts, "h6_results.json")["h6a_primary_daily"]["pair_bias"]["criterion_i"]
    rows.append(_row("H6", "pair-bias coefficient, daily", h6["p"]))

    for name, label in (("live_predictions_score_v1.json", "deployed V1 model"),
                        ("live_predictions_score_release_v20.json", "release model V2.0"),
                        ("live_predictions_score_release_v21.json", "release model V2.1 (4 events; not interpretable)")):
        rows.append(_row("prospective", f"sign accuracy vs coin, {label}", _load(artifacts, name)["p_vs_coin"]))
    return rows


def fmt_p(x: float | None) -> str:
    if x is None:
        return "--"
    if x >= 0.001:
        return f"{x:.3f}"
    mantissa, exponent = f"{x:.1e}".split("e")
    return f"${float(mantissa):.1f}\\times10^{{{int(exponent)}}}$"


def render_latex(rows: list[dict]) -> str:
    lines = ["\\begin{tabular}{@{}p{0.60\\columnwidth}rr@{}}", "\\toprule",
             "Test & $p$ & $q$ (BH)\\\\", "\\midrule"]
    family = None
    for r in rows:
        if r["family"] != family:
            if family is not None:
                lines.append("\\midrule")
            lines.append(f"\\multicolumn{{3}}{{@{{}}l}}{{\\textit{{{r['family']}}}}}\\\\")
            family = r["family"]
        p = r["note"] if r["note"] and r["p"] is None else fmt_p(r["p"])
        lines.append(f"\\quad {r['test']} & {p} & {fmt_p(r['q'])}\\\\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(lines) + "\n"


def main() -> int:
    rows = build_rows()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render_latex(rows), encoding="utf-8")
    finite = sum(1 for r in rows if r["p"] is not None and math.isfinite(r["p"]))
    print(f"Wrote {OUTPUT} ({len(rows)} rows, {finite} with a p-value)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
