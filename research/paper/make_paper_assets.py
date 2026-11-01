"""Generate the paper's LaTeX tables and figures from the result artifacts (no hand-copied numbers).

Reads  research/artifacts/{reaction_eval.json, positive_control.json, h2h3.json}
       research/artifacts/gdelt_shards/*.parquet (timestamps only: the daily-sample defect figure)
Writes research/paper/latex/tables/*.tex and research/paper/latex/figures/*.pdf

Run: python -m research.paper.make_paper_assets
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

RESEARCH = Path(__file__).resolve().parents[1]
ARTIFACTS = RESEARCH / "artifacts"
OUT = Path(__file__).resolve().parent / "latex"
TABLES, FIGURES = OUT / "tables", OUT / "figures"

RELEASE_ORDER = ["release_1m", "release_5m", "release_15m", "release_30m", "release_60m", "release_120m"]
ARRIVAL_ORDER = ["arrival_15+15m", "arrival_15+45m", "arrival_15+105m"]
LABEL = {"release_1m": "release, 0--1", "release_5m": "release, 0--5", "release_15m": r"release, 0--15$^\dagger$",
         "release_30m": "release, 0--30", "release_60m": "release, 0--60", "release_120m": "release, 0--120",
         "arrival_15+15m": "arrival, 15--30", "arrival_15+45m": r"arrival, 15--60$^\dagger$",
         "arrival_15+105m": "arrival, 15--120"}


def _pct(x, digits=1):
    return "--" if x is None else f"{100 * x:.{digits}f}"


def _ci(ci):
    return "" if not ci else f" [{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]"


def _num(x, digits=2):
    return "--" if x is None else f"{x:.{digits}f}"


def _p(x):
    if x is None:
        return "--"
    return "$<$0.001" if x < 0.001 else f"{x:.3f}"


def _write(name: str, body: str) -> None:
    TABLES.mkdir(parents=True, exist_ok=True)
    (TABLES / name).write_text(body, encoding="utf-8")


# ---------------------------------------------------------------- tables

def table_direction(ev: dict) -> None:
    rows = []
    for key in RELEASE_ORDER + ARRIVAL_ORDER:
        d = ev["targets"][key]["direction"]
        p = d["model"].get("p_vs_coin")
        rows.append(f"{LABEL[key]} & {_pct(d['model']['accuracy'])}{_ci(d['model']['ci95'])} & "
                    f"{_num(d['model']['auc'])} & {_pct(d['theory_rule']['accuracy'])}{_ci(d['theory_rule']['ci95'])} & "
                    f"{_pct(d['majority']['accuracy'])} & {_p(p)} & {d['rows']:,} & {d['moments']} \\\\")
        if key == RELEASE_ORDER[-1]:
            rows.append(r"\midrule")
    _write("direction.tex", "\n".join([
        r"\begin{tabular}{lrrrrrrr}", r"\toprule",
        r"Window (min after release) & Model acc. [95\% CI] & AUC & Theory rule acc. [95\% CI] & Majority & $p$ (model vs.\ 50\%) & Rows & Moments \\",
        r"\midrule", *rows, r"\bottomrule", r"\end{tabular}", ""]))


def table_magnitude(ev: dict) -> None:
    rows = []
    for key in ("release_15m", "release_60m", "arrival_15+45m"):
        m = ev["targets"][key]["magnitude"]
        def cell(name):
            e = m[name]
            diff = e.get("model_minus_baseline_abs_error")
            d = "" if not diff else f" ({diff['mean']:+.2f}, $p$={_p(diff['p'])})"
            return f"{_num(e['mae_bps'])} / {_num(e['spearman'])}{d}"
        rows.append(f"{LABEL[key]} & {_num(m['mean_abs_move_bps'], 1)} & {cell('model')} & {cell('pair_mean')} & "
                    f"{cell('cell_mean')} & {cell('v_fit')} \\\\")
    _write("magnitude.tex", "\n".join([
        r"\begin{tabular}{lrllll}", r"\toprule",
        r"Window & Mean $|r|$ & Model & Pair mean & Cell mean & Same-clock $v$ \\",
        r"\midrule", *rows, r"\bottomrule", r"\end{tabular}", ""]))


def table_confidence(ev: dict) -> None:
    rows = []
    for key in ("release_1m", "release_15m", "release_60m", "arrival_15+45m"):
        c = ev["targets"][key]["confidence"]
        cov80, cov90 = c.get("coverage_80") or {}, c.get("coverage_90") or {}
        rows.append(f"{LABEL[key]} & {_num(c['ece'], 3)} & {_num(c['brier'], 3)} & {_num(c['brier_constant_rate'], 3)} & "
                    f"{_pct(c['mean_p'])} & {_pct(c['hit_rate'])} & {_pct(cov80.get('coverage'))} & "
                    f"{_pct(cov90.get('coverage'))} & {_num(cov90.get('mean_width_bps'), 1)} \\\\")
    _write("confidence.tex", "\n".join([
        r"\begin{tabular}{lrrrrrrrr}", r"\toprule",
        r"Window & ECE & Brier & Brier (const.) & Mean $p$ & Hit rate & Cov.\ 80\% & Cov.\ 90\% & 90\% width (bps) \\",
        r"\midrule", *rows, r"\bottomrule", r"\end{tabular}", ""]))


def table_affected(ev: dict) -> None:
    a = ev["rq1_affected"]
    pairs = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF"]
    lines = [r"\begin{tabular}{lrrrr}", r"\toprule", "Release currency & " + " & ".join(pairs) + r" \\", r"\midrule"]
    for cur, by_pair in a["by_release_currency"].items():
        lines.append(f"{cur} & " + " & ".join(_pct(by_pair.get(p), 0) if p in by_pair else "--" for p in pairs) + r" \\")
    lines += [r"\midrule",
              rf"\multicolumn{{5}}{{l}}{{Currency rule: precision {_pct(a['rule']['precision'])}\%, recall "
              rf"{_pct(a['rule']['recall'])}\%, F1 {_num(a['rule']['f1'])}; AUC of expected move {_num(a['auc_expected_move'])}}} \\",
              r"\bottomrule", r"\end{tabular}", ""]
    _write("affected.tex", "\n".join(lines))


def table_positive_control(pc: dict) -> None:
    pooled = pc.get("pooled", {})
    rows = []
    for pair in ("EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF"):
        h1 = (pooled.get(pair) or {}).get("1m") or {}
        beta, p, r2 = h1.get("beta_bps"), h1.get("p"), h1.get("r2")
        n, cl = h1.get("n"), h1.get("clusters")
        rows.append(f"{pair} & {_num(beta)} & {_p(p)} & {_num(r2)} & {n if n else '--'} & {cl if cl else '--'} \\\\")
    _write("positive_control.tex", "\n".join([
        r"\begin{tabular}{lrrrrr}", r"\toprule",
        r"Pair & $\beta$ (bps/$\sigma$), 1 min & $p$ & $R^2$ & $n$ & Release clusters \\",
        r"\midrule", *rows, r"\bottomrule", r"\end{tabular}", ""]))


def table_h2(h2h3: dict) -> None:
    cells = [c for c in h2h3["h2"]["cells"] if c["horizon"] in (1, 15, 60, 120)]
    by_cat: dict[str, dict] = {}
    for c in cells:
        by_cat.setdefault(c["category"], {})[c["horizon"]] = c
    rows = []
    for cat in sorted(by_cat, key=lambda k: -max(v["car_bps"] for v in by_cat[k].values())):
        h = by_cat[cat]
        clusters = max(v["clusters"] for v in h.values())
        rows.append(f"{cat.replace('_', ' ')} & " + " & ".join(
            f"{h[k]['car_bps']:.1f}" + (r"$^{*}$" if h[k].get("survives") else "") if k in h else "--"
            for k in (1, 15, 60, 120)) + f" & {clusters} \\\\")
    _write("h2.tex", "\n".join([
        r"\begin{tabular}{lrrrrr}", r"\toprule",
        r"Category & 1 min & 15 min & 60 min & 120 min & Release dates \\",
        r"\midrule", *rows, r"\bottomrule", r"\end{tabular}", ""]))


# ---------------------------------------------------------------- figures

def _savefig(fig, name: str) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURES / name, bbox_inches="tight")
    plt.close(fig)


def figure_gdelt_defect() -> None:
    files = sorted(glob.glob(str(ARTIFACTS / "gdelt_shards" / "*.parquet")))
    if not files:
        return
    ts = pd.concat([pd.read_parquet(f, columns=["timestamp"]) for f in files])["timestamp"]
    hours = pd.to_datetime(ts, utc=True).dt.hour.value_counts().reindex(range(24), fill_value=0)
    fig, ax = plt.subplots(figsize=(6, 2.6))
    ax.bar(hours.index, 100 * hours / hours.sum(), color="#555555")
    ax.set_xlabel("Hour of GDELT timestamp (UTC)")
    ax.set_ylabel("% of kept headlines")
    ax.set_xticks(range(0, 24, 3))
    ax.set_title(f"Daily DOC requests: {len(files)} days, {len(ts):,} headlines", fontsize=9)
    _savefig(fig, "gdelt_daily_defect.pdf")


def figure_horizon_profile(ev: dict) -> None:
    prof = ev["horizon_profile"]
    rel = [(h, prof[f"r{h}"]) for h in (1, 5, 15, 30, 60, 120) if prof.get(f"r{h}")]
    arr = [(15 + h, prof[f"a{h}"]) for h in (15, 45, 105) if prof.get(f"a{h}")]
    fig, ax = plt.subplots(figsize=(6, 2.8))
    ax.errorbar([h for h, _ in rel], [v["mean"] for _, v in rel], yerr=[1.96 * v["se"] for _, v in rel],
                marker="o", capsize=3, color="black", label="from release")
    ax.errorbar([h for h, _ in arr], [v["mean"] for _, v in arr], yerr=[1.96 * v["se"] for _, v in arr],
                marker="s", capsize=3, color="#999999", label="from article arrival (release + 15 min)")
    ax.axhline(0, color="#cccccc", linewidth=0.8)
    ax.set_xscale("log")
    ax.set_xticks([1, 5, 15, 30, 60, 120])
    ax.set_xticklabels(["1", "5", "15", "30", "60", "120"])
    ax.set_xlabel("Minutes after the release (end of window)")
    ax.set_ylabel("Move in predicted direction (bps)")
    ax.legend(fontsize=8, frameon=False)
    _savefig(fig, "horizon_profile.pdf")


def figure_reliability(ev: dict) -> None:
    fig, ax = plt.subplots(figsize=(3.4, 3.2))
    for key, style in (("release_15m", "o-"), ("release_1m", "s--")):
        bins = ev["targets"][key]["confidence"]["reliability"]
        ax.plot([b["mean_p"] for b in bins], [b["hit_rate"] for b in bins], style, color="black" if key == "release_15m" else "#888888",
                label=LABEL[key].replace(r"$^\dagger$", "").replace("--", "–"), markersize=4)
    ax.plot([0.4, 1.0], [0.4, 1.0], color="#cccccc", linewidth=0.8)
    ax.set_xlabel("Predicted P(direction correct)")
    ax.set_ylabel("Observed hit rate")
    ax.legend(fontsize=7, frameon=False)
    _savefig(fig, "reliability.pdf")


def main() -> int:
    ev = json.loads((ARTIFACTS / "reaction_eval.json").read_text(encoding="utf-8"))
    pc = json.loads((ARTIFACTS / "positive_control.json").read_text(encoding="utf-8"))
    h2h3 = json.loads((ARTIFACTS / "h2h3.json").read_text(encoding="utf-8"))
    table_direction(ev)
    table_magnitude(ev)
    table_confidence(ev)
    table_affected(ev)
    table_positive_control(pc)
    table_h2(h2h3)
    figure_gdelt_defect()
    figure_horizon_profile(ev)
    figure_reliability(ev)
    print("tables:", sorted(p.name for p in TABLES.glob("*.tex")))
    print("figures:", sorted(p.name for p in FIGURES.glob("*.pdf")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
