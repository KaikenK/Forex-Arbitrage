"""Render the Arbex pipeline architecture as a slide-ready PNG."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

OUT = Path(__file__).resolve().parent / "results" / "figures" / "fig_architecture.png"
OUT.parent.mkdir(parents=True, exist_ok=True)

INK = "#241f1a"
TEAL = "#0f6b5c"
CLAY = "#b0561e"
AMBER = "#b8862c"
SLATE = "#5a6b73"
PAPER = "#faf7f1"

LAYERS = [
    ("1 · Ingestion", TEAL, [
        "6 session-aware synthetic\nUSD/INR feeds\n(Bloomberg / Reuters ×\nTokyo / London / New York)",
        "TickNormalizer\n→ common schema,\nlatency-adjusted",
    ]),
    ("2 · Alignment & detection", AMBER, [
        "TickAligner\n20 ms micro-batch\n(phantom-arbitrage guard)",
        "ArbitrageEngine\ncross-source +\nsession inefficiency",
    ]),
    ("3 · Institutional scoring", CLAY, [
        "OpportunityTracker\nephemeral / flickering /\npersistent",
        "SimulatedExecutionFilter\nL2 depth + latency →\nviable / risky / unlikely",
        "OpportunityRanker\ncomposite leaderboard",
    ]),
    ("4 · Transport & delivery", SLATE, [
        "Redis pub/sub\narbex.raw_opps →\narbex.scored_opps",
        "SemanticEngine\n(separate microservice,\nnews context)",
        "WebSocket → Next.js\nresearch dashboard",
    ]),
]

fig, ax = plt.subplots(figsize=(12.6, 7.4))
ax.set_xlim(0, 100)
ax.set_ylim(0, 100)
ax.axis("off")
fig.patch.set_facecolor("white")

band_h = 20.5
box_h = 12.0
label_gap = 4.6
y = 92.0
box_centres: list[list[tuple[float, float]]] = []

for name, color, boxes in LAYERS:
    ax.text(2.5, y, name, fontsize=11.5, fontweight="bold", color=color, va="top")
    cy = y - label_gap - box_h / 2

    n = len(boxes)
    span = 95.0
    bw = min(27.0, span / n - 3)
    total = n * bw + (n - 1) * 3
    x = 2.5 + (span - total) / 2
    centres = []
    for text in boxes:
        cx = x + bw / 2
        ax.add_patch(FancyBboxPatch(
            (x, cy - box_h / 2), bw, box_h,
            boxstyle="round,pad=0.25,rounding_size=1.0",
            linewidth=1.5, edgecolor=color, facecolor="white", zorder=2))
        ax.text(cx, cy, text, fontsize=8.5, ha="center", va="center",
                color=INK, zorder=3, linespacing=1.32)
        centres.append((cx, cy))
        x += bw + 3
    box_centres.append(centres)
    y -= band_h

for i in range(len(LAYERS) - 1):
    src_y = box_centres[i][0][1] - box_h / 2
    dst_y = box_centres[i + 1][0][1] + box_h / 2
    ax.add_patch(FancyArrowPatch(
        (50, src_y - 0.4), (50, dst_y + 0.4),
        arrowstyle="-|>", mutation_scale=17, linewidth=1.7,
        color=SLATE, zorder=4))

ax.text(50, 99.0, "Arbex — event-driven detection pipeline (synthetic USD/INR research mode)",
        fontsize=13, fontweight="bold", ha="center", va="top", color=INK)

fig.tight_layout()
fig.savefig(OUT, dpi=130, bbox_inches="tight")
print(f"wrote {OUT}")
