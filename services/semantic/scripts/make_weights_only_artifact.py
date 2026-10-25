"""Convert an existing linear reaction artifact to the weights-only format.

`training/reaction/export.py` already writes new linear artifacts weights-only. This
converts one exported before that change, without retraining: the training rows are
dropped and replaced by the counts the runtime needs (`available_horizons`,
`horizon_row_counts`, `training_row_count`). The coefficients are copied unchanged, and
the result is checked by loading both files with the runtime loader.

Usage:
  python scripts/make_weights_only_artifact.py <input.json> <output.json>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from market.reaction_artifact import load_reaction_artifact  # noqa: E402

LINEAR_KEYS = ("direction_classes", "direction_intercepts", "direction_coefficients",
               "magnitude_intercept", "magnitude_coefficients")


def convert(source: Path, target: Path) -> dict[str, object]:
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("model_family") != "linear" or any(payload.get(key) is None for key in LINEAR_KEYS):
        raise SystemExit(f"{source} is not a complete linear artifact; k-NN artifacts need their rows.")
    rows = payload.get("training_rows") or []
    if rows:
        counts: dict[str, int] = {}
        for row in rows:
            key = str(int(row["horizon_minutes"]))
            counts[key] = counts.get(key, 0) + 1
        payload["training_row_count"] = len(rows)
        payload["available_horizons"] = sorted(int(key) for key in counts)
        payload["horizon_row_counts"] = counts
    payload["weights_only"] = True
    payload["training_rows"] = []
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    before, after = load_reaction_artifact(source), load_reaction_artifact(target)
    for key in LINEAR_KEYS + ("horizon_coefficients", "direction_calibration_temperature"):
        if getattr(before, key, None) != getattr(after, key, None):
            raise SystemExit(f"conversion changed {key}")
    if before.available_horizons() != after.available_horizons():
        raise SystemExit("conversion changed the available horizons")
    return {"rows_dropped": len(rows), "bytes_before": source.stat().st_size, "bytes_after": target.stat().st_size,
            "horizons": after.available_horizons()}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    print(convert(Path(sys.argv[1]), Path(sys.argv[2])))
