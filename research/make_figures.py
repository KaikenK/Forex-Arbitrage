"""
Arbex — figure generation from research-harness output.

Reads ``research/results/<run_id>/{summary.json,opportunities.csv}`` and writes
publication-style PNGs to ``research/results/figures/``.

Usage:
    python -m research.make_figures
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
FIGDIR = RESULTS / "figures"
FIGDIR.mkdir(parents=True, exist_ok=True)

# --- house style -----------------------------------------------------------
INK = "#241f1a"
GRID = "#d9d3c4"
TEAL = "#0f6b5c"
CLAY = "#b0561e"
AMBER = "#b8862c"
SLATE = "#5a6b73"
SEQ = [TEAL, AMBER, CLAY]

plt.rcParams.update({
    "figure.dpi": 130,
    "savefig.dpi": 130,
    "font.size": 11,
    "font.family": "DejaVu Sans",
    "axes.edgecolor": INK,
    "axes.labelcolor": INK,
    "axes.titlecolor": INK,
    "text.color": INK,
    "xtick.color": INK,
    "ytick.color": INK,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.7,
    "axes.axisbelow": True,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
})


def load_summary(run_id: str) -> dict:
    return json.loads((RESULTS / run_id / "summary.json").read_text())


def load_opps(run_id: str) -> list[dict]:
    p = RESULTS / run_id / "opportunities.csv"
    if not p.exists():
        return []
    with p.open() as fh:
        return list(csv.DictReader(fh))


def _save(fig, name: str) -> None:
    fig.tight_layout()
    out = FIGDIR / name
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"  wrote {out.relative_to(ROOT)}")


# -------------------------------------------------------------------------
def fig_persistence(main: str = "main") -> None:
    s = load_summary(main)
    d = s["persistence_distribution_per_episode"]
    total = sum(d.values()) or 1
    labels = ["Ephemeral\n(<50 ms)", "Flickering\n(50–300 ms)", "Persistent\n(>300 ms)"]
    vals = [d["ephemeral"], d["flickering"], d["persistent"]]
    pct = [100 * v / total for v in vals]

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    bars = ax.bar(labels, vals, color=SEQ, width=0.62)
    for b, v, p in zip(bars, vals, pct):
        ax.text(b.get_x() + b.get_width() / 2, v + total * 0.015,
                f"{v:,}\n{p:.1f}%", ha="center", va="bottom", fontsize=10)
    ax.set_ylabel("opportunity episodes")
    ax.set_title(f"Opportunity persistence — {total:,} episodes over "
                 f"{s['run']['sim_minutes']:.0f} min (seed {s['run']['seed']})")
    ax.set_ylim(0, max(vals) * 1.18)
    ax.grid(axis="x")
    _save(fig, "fig_persistence.png")


def fig_cost_filter(main: str = "main") -> None:
    s = load_summary(main)
    cf = s["cost_filter_effect"]
    stages = ["Profitable\non paper", "Viable after L2 depth\n+ latency cost"]
    vals = [cf["profitable_on_paper"], cf["still_viable_after_L2_and_latency"]]

    fig, ax = plt.subplots(figsize=(6.2, 3.9))
    bars = ax.barh(stages[::-1], vals[::-1], color=[TEAL, SLATE], height=0.55)
    for b, v in zip(bars, vals[::-1]):
        ax.text(v + max(vals) * 0.01, b.get_y() + b.get_height() / 2,
                f"{v:,}", va="center", fontsize=11)
    ax.set_xlabel("detections")
    ax.set_title(f"Execution feasibility filter removes "
                 f"{cf['pct_filtered_out']:.0f}% of paper opportunities")
    ax.grid(axis="y")
    _save(fig, "fig_cost_filter.png")


def fig_by_session(main: str = "main") -> None:
    s = load_summary(main)
    bs = s["by_session"]
    spread = s.get("session_spread_pips", {})
    order = [k for k in ("TOKYO", "LONDON", "NEW_YORK") if k in bs]
    det = [bs[k]["detections"] for k in order]
    spr = [spread.get(k, 0.0) for k in order]

    fig, ax1 = plt.subplots(figsize=(6.6, 4.0))
    x = range(len(order))
    b = ax1.bar([i - 0.2 for i in x], det, width=0.4, color=TEAL, label="detections")
    ax1.set_ylabel("raw detections", color=TEAL)
    ax1.tick_params(axis="y", labelcolor=TEAL)
    ax1.set_xticks(list(x))
    ax1.set_xticklabels([k.title().replace("_", " ") for k in order])
    for bar, v in zip(b, det):
        ax1.text(bar.get_x() + bar.get_width() / 2, v, f"{v:,}", ha="center", va="bottom", fontsize=9)

    ax2 = ax1.twinx()
    ax2.bar([i + 0.2 for i in x], spr, width=0.4, color=AMBER, label="avg spread")
    ax2.set_ylabel("avg spread (pips)", color=AMBER)
    ax2.tick_params(axis="y", labelcolor=AMBER)
    ax2.grid(False)
    ax1.set_title("Detections and spread by trading session")
    _save(fig, "fig_by_session.png")


def fig_profit_hist(main: str = "main") -> None:
    rows = load_opps(main)
    if not rows:
        return
    profits = [float(r["profit_pips"]) for r in rows]
    nets = [float(r["net_profit_pips"]) for r in rows]

    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    bins = [i for i in range(0, 21)]
    ax.hist(profits, bins=bins, color=TEAL, alpha=0.85, label="gross")
    ax.hist(nets, bins=bins, color=CLAY, alpha=0.6, label="net of costs")
    ax.axvline(3.0, color=INK, ls="--", lw=1, label="min-profit threshold (3 pips)")
    ax.set_xlabel("profit (pips)")
    ax.set_ylabel("detections")
    ax.set_title("Detected opportunity profit distribution")
    ax.legend(frameon=False, fontsize=9)
    _save(fig, "fig_profit_hist.png")


def fig_alignment_ablation() -> None:
    runs = [("abl_w20", 20), ("abl_w50", 50), ("abl_w100", 100),
            ("main", 200), ("abl_w500", 500)]
    xs, det_min, persist_frac, viable_frac = [], [], [], []
    for rid, w in runs:
        p = RESULTS / rid / "summary.json"
        if not p.exists():
            continue
        s = json.loads(p.read_text())
        xs.append(w)
        det_min.append(s["detection"]["detections_per_sim_minute"])
        ep = s["persistence_distribution_per_episode"]
        tot = sum(ep.values()) or 1
        persist_frac.append(100 * ep["persistent"] / tot)
        cf = s["cost_filter_effect"]
        viable_frac.append(100 - cf["pct_filtered_out"])

    fig, ax1 = plt.subplots(figsize=(7.0, 4.3))
    ax1.plot(xs, det_min, "o-", color=TEAL, lw=2, label="detections / min")
    ax1.set_xlabel("tick-alignment window (ms)")
    ax1.set_ylabel("detections / sim-minute", color=TEAL)
    ax1.tick_params(axis="y", labelcolor=TEAL)
    ax1.set_xscale("log")
    ax1.set_xticks(xs)
    ax1.set_xticklabels([str(x) for x in xs])

    ax2 = ax1.twinx()
    ax2.plot(xs, persist_frac, "s--", color=CLAY, lw=2, label="% episodes 'persistent'")
    ax2.plot(xs, viable_frac, "^:", color=AMBER, lw=2, label="% detections 'viable'")
    ax2.set_ylabel("percent", color=INK)
    ax2.grid(False)
    ax2.axvspan(160, 260, color=TEAL, alpha=0.08)
    ax2.text(200, ax2.get_ylim()[1] * 0.92, "operating\npoint", ha="center", fontsize=8, color=TEAL)

    lines = ax1.get_lines() + ax2.get_lines()
    ax1.legend(lines, [l.get_label() for l in lines], frameon=False, fontsize=8, loc="upper right")
    ax1.set_title("Tightening the alignment window inflates phantom arbitrage")
    _save(fig, "fig_alignment_ablation.png")


def fig_seed_variance() -> None:
    seeds = ["main", "seed7", "seed123", "seed2025"]
    got = [(s, load_summary(s)) for s in seeds if (RESULTS / s / "summary.json").exists()]
    if len(got) < 2:
        return
    labels = ["detections\n/ min", "episodes\n/ min", "% filtered\nby cost", "% episodes\npersistent"]

    def metrics(s):
        ep = s["persistence_distribution_per_episode"]
        tot = sum(ep.values()) or 1
        return [
            s["detection"]["detections_per_sim_minute"],
            s["detection"]["episodes_per_sim_minute"],
            s["cost_filter_effect"]["pct_filtered_out"],
            100 * ep["persistent"] / tot,
        ]

    series = [metrics(s) for _, s in got]
    means = [sum(col) / len(col) for col in zip(*series)]
    lo = [min(col) for col in zip(*series)]
    hi = [max(col) for col in zip(*series)]
    err = [
        [max(0.0, m - l) for m, l in zip(means, lo)],
        [max(0.0, h - m) for h, m in zip(means, hi)],
    ]

    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    x = range(len(labels))
    ax.bar(x, means, yerr=err, capsize=5, color=[TEAL, TEAL, SLATE, CLAY], width=0.6)
    for i, m in enumerate(means):
        ax.text(i, m, f"{m:.1f}", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_title(f"Metric stability across {len(got)} random seeds (30 min each)")
    ax.grid(axis="x")
    _save(fig, "fig_seed_variance.png")


def fig_eod_basis(run_id: str | None = None) -> None:
    eod_root = RESULTS / "eod"
    if run_id:
        candidates = [eod_root / run_id]
    else:  # prefer a non-sample run if one exists
        runs = sorted(d for d in eod_root.glob("*") if (d / "basis_eod.jsonl").exists())
        candidates = [d for d in runs if d.name != "sample"] or runs
    if not candidates:
        return
    src = candidates[-1]
    rows = [json.loads(l) for l in (src / "basis_eod.jsonl").read_text().splitlines() if l.strip()]
    if not rows:
        return
    is_sample = src.name == "sample"
    dates = [r["trade_date"] for r in rows]
    pair = "onshore_offshore" if any(
        "onshore_offshore" in r["basis_pips"] for r in rows) else "onshore_spot"
    oo = [r["basis_pips"].get(pair) for r in rows]
    xs = list(range(len(dates)))

    # dislocation events (from the detector)
    ev_path = src / "events.jsonl"
    ev_days = set()
    if ev_path.exists():
        import datetime as _dt
        for line in ev_path.read_text().splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e["leg_pair"] == pair:
                ev_days.add(_dt.datetime.utcfromtimestamp(e["ts"]).date().isoformat())

    fig, ax = plt.subplots(figsize=(8.4, 4.0))
    ax.axhline(0, color=INK, lw=1)
    ax.axhspan(-2, 2, color=SLATE, alpha=0.10)   # min-basis threshold band
    ax.plot(xs, oo, "-o", color=TEAL, lw=1.6, ms=3.5)
    ev_x = [i for i, d in enumerate(dates) if d in ev_days]
    if ev_x:
        ax.plot(ev_x, [oo[i] for i in ev_x], "o", color=CLAY, ms=7,
                mfc="none", mew=1.6, label=f"dislocation event ({len(ev_x)})")
        ax.legend(frameon=False, fontsize=8, loc="lower left")
    # annotate only the few most extreme points (sparse for long series)
    vals = [(i, v) for i, v in enumerate(oo) if v is not None]
    extremes = sorted(vals, key=lambda t: abs(t[1]), reverse=True)[:min(6, len(vals))]
    for i, v in extremes:
        ax.annotate(f"{v:+.0f}", (i, v), fontsize=7.5, ha="center",
                    va="bottom" if v > 0 else "top", color=CLAY)
    step = max(1, len(dates) // 8)
    ax.set_xticks(xs[::step])
    ax.set_xticklabels([dates[i][5:] for i in xs[::step]], rotation=0, fontsize=8)
    pair_label = pair.replace("_", " − ")
    ax.set_ylabel(f"{pair_label} basis (pips)")
    ax.set_title(f"EOD USD/INR {pair_label} basis  (Option A, daily marks)  ·  {src.name}")
    if is_sample:
        fig.text(0.5, -0.02, "SAMPLE data — replace with real NSE / CME / RBI files",
                 ha="center", fontsize=8, color=CLAY, style="italic")
    _save(fig, "fig_eod_basis.png")


def write_report() -> None:
    """Consolidated numbers table for the review deck."""
    main = load_summary("main")
    lines = ["# Arbex - experiment results\n",
             f"_Generated from `research/results/`. Harness: fake-clock, seeded, "
             f"git `{json.loads((RESULTS / 'main' / 'manifest.json').read_text())['git_sha']}`._\n",
             "## Headline run (`main`: 30 min, seed 42, 200 ms window, 6 synthetic USD/INR feeds)\n",
             "| metric | value |", "|---|---|",
             f"| ticks processed | {main['throughput']['ticks_processed']:,} "
             f"({main['throughput']['ticks_per_sim_minute']:,.0f}/min) |",
             f"| raw detections | {main['detection']['raw_detections']:,} "
             f"({main['detection']['detections_per_sim_minute']}/min) |",
             f"| opportunity episodes | {main['detection']['opportunity_episodes']:,} |",
             f"| persistence (episodes) | "
             f"{main['persistence_distribution_per_episode']} |",
             f"| execution verdicts (detections) | "
             f"{main['execution_verdicts_per_detection']} |",
             f"| cost filter removes | {main['cost_filter_effect']['pct_filtered_out']}% "
             f"of paper opportunities |",
             f"| gross profit (pips) | mean {main['profit_pips']['mean_gross']}, "
             f"median {main['profit_pips']['median_gross']}, max {main['profit_pips']['max_gross']} |",
             f"| by session (detections) | "
             + ", ".join(f"{k} {v['detections']}" for k, v in main["by_session"].items()) + " |",
             "\n## Alignment-window ablation (30 min, seed 42)\n",
             "| window ms | detections/min | % episodes persistent | % detections viable |",
             "|---|---|---|---|"]
    for rid, w in [("abl_w20", 20), ("abl_w50", 50), ("abl_w100", 100),
                   ("main", 200), ("abl_w500", 500)]:
        p = RESULTS / rid / "summary.json"
        if not p.exists():
            continue
        s = json.loads(p.read_text())
        ep = s["persistence_distribution_per_episode"]
        tot = sum(ep.values()) or 1
        lines.append(f"| {w} | {s['detection']['detections_per_sim_minute']} | "
                     f"{100 * ep['persistent'] / tot:.1f}% | "
                     f"{100 - s['cost_filter_effect']['pct_filtered_out']:.1f}% |")
    lines.append("\n## Seed variance (30 min, 200 ms window)\n")
    lines.append("| seed | detections | episodes | % filtered | persistent episodes |")
    lines.append("|---|---|---|---|---|")
    for rid in ["main", "seed7", "seed123", "seed2025"]:
        p = RESULTS / rid / "summary.json"
        if not p.exists():
            continue
        s = json.loads(p.read_text())
        ep = s["persistence_distribution_per_episode"]
        lines.append(f"| {s['run']['seed']} | {s['detection']['raw_detections']:,} | "
                     f"{s['detection']['opportunity_episodes']:,} | "
                     f"{s['cost_filter_effect']['pct_filtered_out']}% | {ep['persistent']} |")
    (RESULTS / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  wrote {(RESULTS / 'REPORT.md').relative_to(ROOT)}")


def main() -> None:
    print("Generating figures...")
    fig_persistence()
    fig_cost_filter()
    fig_by_session()
    fig_profit_hist()
    fig_alignment_ablation()
    fig_seed_variance()
    fig_eod_basis()
    write_report()
    print(f"\nAll figures in {FIGDIR}")


if __name__ == "__main__":
    main()
