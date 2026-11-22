"""Decay figure for the IEEE paper: how much of the release reaction is still predictable later.

Panel (a): mean move in the predicted direction (bps) against the end of the measurement window,
for release-anchored windows r(0, h) and arrival-anchored windows r(15, 15 + h), with 95%
intervals (mean +/- 1.96 se, standard errors clustered by release moment as in the artifact).
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

Z95 = 1.96
ARRIVAL_OFFSET_MIN = 15
RELEASE_HORIZONS = (1, 5, 15, 30, 60, 120)
ARRIVAL_HORIZONS = (15, 45, 105)
LATENCY_POINTS = (1, 2, 5, 15)

# horizon_profile key -> targets key holding the theory-rule accuracy for the same window
_TARGET_KEY = {**{f"r{h}": f"release_{h}m" for h in RELEASE_HORIZONS},
               **{f"a{h}": f"arrival_{ARRIVAL_OFFSET_MIN}+{h}m" for h in ARRIVAL_HORIZONS}}


def _point(profile: dict, targets: dict, key: str, window_end: int) -> dict:
    cell = profile[key]
    mean, se = float(cell["mean"]), float(cell["se"])
    theory = targets[_TARGET_KEY[key]]["direction"]["theory_rule"]["accuracy"]
    return {"key": key, "window_end_min": window_end, "mean_bps": mean, "se_bps": se,
            "lo_bps": mean - Z95 * se, "hi_bps": mean + Z95 * se, "n": int(cell["n"]),
            "theory_accuracy": float(theory)}


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


def decay_data(reaction_eval: dict, arrival_latency: dict) -> dict:
    """Pure data preparation; no plotting."""
    profile, targets = reaction_eval["horizon_profile"], reaction_eval["targets"]
    release = [_point(profile, targets, f"r{h}", h) for h in RELEASE_HORIZONS]
    arrival = [_point(profile, targets, f"a{h}", ARRIVAL_OFFSET_MIN + h) for h in ARRIVAL_HORIZONS]
    return {"release": release, "arrival": arrival,
            "effective": latency_cdf(arrival_latency["effective_latency"]),
            "receipt": latency_cdf(arrival_latency["receipt_latency"])}


def load_data(artifacts: Path = ARTIFACTS) -> dict:
    read = lambda name: json.loads((artifacts / name).read_text(encoding="utf-8"))  # noqa: E731
    return decay_data(read("reaction_eval.json"), read("arrival_latency.json"))


def render(data: dict, output: Path = OUTPUT) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    blue, orange, grey = "#1f5fa8", "#c4631a", "#555555"
    plt.rcParams.update({"font.size": 7, "axes.linewidth": 0.6, "pdf.fonttype": 42})
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(3.45, 4.5), gridspec_kw={"height_ratios": [1.25, 1]})

    for series, colour, label, marker in ((data["release"], blue, "release-anchored  r(0, h)", "o"),
                                          (data["arrival"], orange, "arrival-anchored  r(15, 15+h)", "s")):
        x = [p["window_end_min"] for p in series]
        y = [p["mean_bps"] for p in series]
        err = [Z95 * p["se_bps"] for p in series]
        top.errorbar(x, y, yerr=err, color=colour, marker=marker, ms=3.5, lw=1.0, capsize=2, label=label)
    for p in data["release"]:
        top.annotate(f"{100 * p['theory_accuracy']:.0f}%", (p["window_end_min"], p["hi_bps"]),
                     textcoords="offset points", xytext=(0, 3), ha="center", fontsize=6, color=blue)
    top.axhline(0, color=grey, lw=0.6)
    top.margins(y=0.12)
    top.set_xscale("log")
    top.set_xticks([1, 5, 15, 30, 60, 120])
    top.set_xticklabels(["1", "5", "15", "30", "60", "120"])
    top.set_xlabel("end of window, minutes after the release")
    top.set_ylabel("mean move in predicted\ndirection (bps)")
    top.set_title("(a) Release reaction and what remains after arrival", fontsize=7, loc="left")
    top.legend(frameon=False, loc="center right", fontsize=6)
    top.text(0.02, 0.04, "labels: theory-rule accuracy", transform=top.transAxes, fontsize=5.5, color=blue)

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
