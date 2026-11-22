"""Arrival-delay decay: re-run the RQ2 arrival test at several assumed article delays.

The pre-registered arrival test assumes an article appears 15 minutes after its release; the
measured effective latency is longer (arrival_latency.json), and nothing between 1 and 15
minutes was evaluated. This loops the existing `run_reaction_eval` arrival logic over assumed
offsets with a fixed 45-minute window r(offset, offset + 45), the same window as the primary
arrival target, and tabulates direction accuracy, move size and the mean move in the predicted
direction per offset. It reuses `run_reaction_eval.run_arrival_offset` (same panel, same
walk-forward, same cluster rules); it adds no estimator of its own.

Offsets are fixed here, before any run: 0, 1, 2, 5, 10, 15, 30 minutes. Offset 15 reproduces the
pre-registered primary arrival target. Exploratory and descriptive: no decision weight, no
multiple-comparison claim, and not a pre-registered test (it is logged as such in the tracker
before it is run).

    python -m research.run_decay                 # heavy: loads HistData for every offset
    python -m research.run_decay --offsets 0 5   # a subset

Writes artifacts/reaction_eval_arrival<offset>.json (one per offset, by run_reaction_eval) and
artifacts/decay_by_offset.json (the table).
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
OUTPUT = ARTIFACTS / "decay_by_offset.json"

OFFSETS_MIN: tuple[int, ...] = (0, 1, 2, 5, 10, 15, 30)
WINDOW_MIN = 45                      # = ARRIVAL_HORIZONS primary (PRIMARY_ARRIVAL_H)


def window_for(offset_min: int, window_min: int = WINDOW_MIN) -> tuple[int, int]:
    """(start, end) in minutes after the release for an article assumed to arrive at `offset_min`."""
    if offset_min < 0 or window_min <= 0:
        raise ValueError("offset must be >= 0 and the window positive")
    return offset_min, offset_min + window_min


def label_for(offset_min: int, window_min: int = WINDOW_MIN) -> str:
    """Key under which run_reaction_eval stores this target: arrival_<offset>+<window>m."""
    return f"arrival_{offset_min}+{window_min}m"


def summarise(result: dict, offset_min: int, window_min: int = WINDOW_MIN) -> dict:
    """One row of the decay table from a `reaction_eval_arrival<offset>.json` payload."""
    target = result["targets"][label_for(offset_min, window_min)]
    direction, magnitude = target["direction"], target["magnitude"]
    profile = (result.get("horizon_profile") or {}).get(f"a{window_min}") or {}
    start, end = window_for(offset_min, window_min)
    return {"offset_min": offset_min, "window_start_min": start, "window_end_min": end,
            "rows": direction["rows"], "moments": direction["moments"],
            "model_accuracy": direction["model"]["accuracy"], "model_ci95": direction["model"]["ci95"],
            "model_p_vs_coin": direction["model"].get("p_vs_coin"),
            "theory_accuracy": direction["theory_rule"]["accuracy"],
            "theory_ci95": direction["theory_rule"]["ci95"],
            "majority_accuracy": direction["majority"]["accuracy"],
            "mean_abs_move_bps": magnitude["mean_abs_move_bps"],
            "directed_move_mean_bps": profile.get("mean"), "directed_move_se_bps": profile.get("se"),
            "directed_move_p": profile.get("p")}


def run(offsets: tuple[int, ...] = OFFSETS_MIN, *, rebuild: bool = False) -> dict:
    from research import run_reaction_eval as rre      # heavy imports kept out of module import

    rows = []
    for offset in offsets:
        print(f"== offset {offset} min: window {window_for(offset)} ==")
        status = rre.run_arrival_offset(offset, rebuild=rebuild)
        if status != 0:
            raise RuntimeError(f"run_arrival_offset({offset}) returned {status}")
        path = ARTIFACTS / f"reaction_eval_arrival{offset}.json"
        rows.append(summarise(json.loads(path.read_text(encoding="utf-8")), offset))
    table = {"framing": "exploratory, descriptive; offsets fixed before the run; not pre-registered as a test",
             "window_min": WINDOW_MIN, "computed_at": datetime.now(UTC).isoformat(), "offsets": rows}
    OUTPUT.write_text(json.dumps(table, indent=2, default=float), encoding="utf-8")
    print(f"Wrote {OUTPUT.name}")
    return table


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offsets", type=int, nargs="+", default=list(OFFSETS_MIN))
    parser.add_argument("--rebuild-panel", action="store_true")
    args = parser.parse_args()
    run(tuple(args.offsets), rebuild=args.rebuild_panel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
