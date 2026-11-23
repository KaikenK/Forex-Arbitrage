"""Decay figure for the IEEE paper: how fast the model's direction accuracy falls to chance.

Panel (a): model direction accuracy (95% interval) against the assumed article delay, for the seven
offsets of `decay_by_offset.json` (window r(offset, offset + 45 min)); offset 0 is the release
reaction itself and is drawn apart from the article-delay offsets. Exploratory, no correction.
Panel (b): empirical CDF of the delay between a release and the first article about it, from the
shares within 1, 2, 5 and 15 minutes in `arrival_latency.json` (effective latency, n = 9 releases;
receipt latency, n = 19 rows).

Reads committed artifacts only; writes `figures/decay.pdf`. matplotlib is imported inside
`render`, so `decay_data` can be tested without it.

    python -m research.paper.ieee.make_decay_figure
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE.parents[1] / "artifacts"
OUTPUT = HERE / "figures" / "decay.pdf"

OFFSETS_MIN = (0, 1, 2, 5, 10, 15, 30)
LATENCY_POINTS = (1, 2, 5, 15)


def offset_points(decay: dict) -> list[dict]:
    """One point per assumed article delay: model accuracy with its 95% interval."""
    by_offset = {int(o["offset_min"]): o for o in decay["offsets"]}
    if tuple(sorted(by_offset)) != OFFSETS_MIN:
        raise ValueError(f"expected offsets {OFFSETS_MIN}, got {sorted(by_offset)}")
    out = []
    for m in OFFSETS_MIN:
        o = by_offset[m]
        lo, hi = (float(v) for v in o["model_ci95"])
        out.append({"offset_min": m, "accuracy": float(o["model_accuracy"]), "lo": lo, "hi": hi,
                    "p": float(o["model_p_vs_coin"]), "moments": int(o["moments"]),
                    "directed_move_bps": float(o["directed_move_mean_bps"])})
    return out


def latency_cdf(block: dict) -> dict:
    """CDF points (minutes, share) from the reported shares within each limit, starting at 0."""
    xs, ys = [0.0], [0.0]
    for minutes in LATENCY_POINTS:
        share = float(block[f"within_{minutes}_min"])
        if share < ys[-1]:
            raise ValueError("shares within a limit must not decrease")
        xs.append(float(minutes))
        ys.append(share)
    return {"minutes": xs, "share": ys, "n": int(block["n"]), "median_min": float(block["median_min"])}


def decay_data(decay: dict, arrival_latency: dict) -> dict:
    """Pure data preparation; no plotting."""
    return {"offsets": offset_points(decay),
            "effective": latency_cdf(arrival_latency["effective_latency"]),
            "receipt": latency_cdf(arrival_latency["receipt_latency"])}


def load_data(artifacts: Path = ARTIFACTS) -> dict:
    read = lambda name: json.loads((artifacts / name).read_text(encoding="utf-8"))  # noqa: E731
    return decay_data(read("decay_by_offset.json"), read("arrival_latency.json"))


def render(data: dict, output: Path = OUTPUT) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    blue, orange, grey = "#1f5fa8", "#c4631a", "#555555"
    plt.rcParams.update({"font.size": 7, "axes.linewidth": 0.6, "pdf.fonttype": 42})
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(3.45, 4.1), gridspec_kw={"height_ratios": [1.2, 1]})

    pts = data["offsets"]
    xs = list(range(len(pts)))
    top.axhline(50, color=grey, lw=0.6, ls="--")
    top.text(len(pts) - 0.6, 50.3, "chance", fontsize=5.5, color=grey, ha="right", va="bottom")
    rest = pts[1:]
    top.errorbar(xs[1:], [100 * p["accuracy"] for p in rest], color=orange, marker="s", ms=3.5, lw=1.0,
                 capsize=2, yerr=[[100 * (p["accuracy"] - p["lo"]) for p in rest],
                                  [100 * (p["hi"] - p["accuracy"]) for p in rest]],
                 label="article delay (offset > 0)")
    first = pts[0]
    top.errorbar([0], [100 * first["accuracy"]], marker="D", ms=5, color=blue, capsize=2, lw=1.0,
                 yerr=[[100 * (first["accuracy"] - first["lo"])], [100 * (first["hi"] - first["accuracy"])]],
                 label="release reaction (offset 0)")
    top.set_xticks(xs)
    top.set_xticklabels([str(p["offset_min"]) for p in pts])
    top.set_xlabel("assumed article delay, minutes after the release")
    top.set_ylabel("model direction accuracy (%)")
    top.set_title("(a) Direction accuracy by assumed delay (exploratory)", fontsize=7, loc="left")
    top.legend(frameon=False, loc="upper right", fontsize=6)

    for key, colour, label in (("effective", blue, "effective (n = %d releases)"),
                               ("receipt", orange, "receipt (n = %d rows)")):
        cdf = data[key]
        bottom.plot(cdf["minutes"], cdf["share"], "-o", color=colour, lw=1.0, ms=3, label=label % cdf["n"])
        # the median is the 50% point; the dashed segment joins it to the 15-minute share (no data between)
        bottom.plot([cdf["minutes"][-1], cdf["median_min"]], [cdf["share"][-1], 0.5], "--", color=colour, lw=0.8)
        bottom.plot([cdf["median_min"]], [0.5], "D", color=colour, ms=3.5)
    bottom.axvline(15, color=grey, lw=0.5, ls="--")
    bottom.set_xlim(0, 40)
    bottom.set_ylim(0, 1)
    bottom.set_xlabel("minutes from release to first article")
    bottom.set_ylabel("share of articles arrived")
    bottom.set_title("(b) Article arrival", fontsize=7, loc="left")
    bottom.legend(frameon=False, loc="upper left", fontsize=6)

    fig.tight_layout(pad=0.5)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)
    return output


def main() -> int:
    out = render(load_data())
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
