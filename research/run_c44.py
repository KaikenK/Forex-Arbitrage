"""C44 interim (pre-specified 2026-11-21, RESEARCH_TRACKER §6 "C44 AMENDMENT"): a descriptive look at
the cloud sample on or after 2026-12-05. No decision weight; nothing else is examined.

Data: the cloud recorder's output pulled to the laptop (`cloud_pull`, default C:\\Users\\xyreo\\arbex\\cloud_data),
from the first quote (2026-11-21 12:43 UTC) to Friday 2026-12-04 21:00 UTC (the FX week's close).

  I-1 ranking   still-crossed AUC (H6b's locked next-poll outcome, one-sided next quotes dropped as C19) of
                the detector's `raw_confidence_score` vs the recorded, adjusted `confidence_score` on the
                same opportunities; the RQ5 arms (risk, risk + direction, V1 window, v2) computed from the
                detector score as in RQ5; 95 % CIs and differences from a day-cluster bootstrap (2,000
                draws, seed 20261025); realised edge at the next poll.
  I-2 latency   the 2026-11-02 arrival-latency study on the cloud prediction log and calendar archive
                (`measure_arrival_latency.compute`); a* only if >= 10 releases.

The C44 main analysis plan must be logged in §6 before this runs (else the main analysis uses only data
after the interim look); this script refuses to run before the interim window closes.

  python -m research.run_c44 [--cloud-data <dir>]
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from research import measure_arrival_latency as latency
from research.run_h6b import (BOOTSTRAP_DRAWS, day_bootstrap, evaluation_rows, load_opportunities, load_quotes,
                              model_scores, window_scores)

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
RESULTS = ARTIFACTS / "c44_interim_results.json"
DEFAULT_CLOUD = Path(r"C:\Users\xyreo\arbex\cloud_data")
SAMPLE_START = datetime(2026, 11, 21, 12, 43, tzinfo=UTC)
INTERIM_END = datetime(2026, 12, 4, 21, 0, tzinfo=UTC)


def ranking(recordings: Path, *, start: datetime = SAMPLE_START, end: datetime = INTERIM_END,
            draws: int = BOOTSTRAP_DRAWS) -> dict:
    day_dirs = sorted(d for d in recordings.iterdir() if d.is_dir())
    quotes, opportunities = load_quotes(day_dirs), load_opportunities(day_dirs)
    usable, without_raw = [], 0
    for opp in opportunities:
        if opp.get("raw_confidence_score") is None:
            without_raw += 1
            continue
        usable.append({**opp, "adjusted_confidence_score": opp["confidence_score"],
                       "confidence_score": opp["raw_confidence_score"]})         # detector = raw
    rows, skipped = evaluation_rows(usable, quotes, one_sided="drop", window_start=start, window_end=end,
                                    extra=lambda o: {"adjusted": float(o["adjusted_confidence_score"])})
    skipped["no_raw_confidence_score"] = without_raw
    out: dict = {"rows": len(rows), "skipped": skipped,
                 "window": [start.isoformat(), end.isoformat()]}
    labels = [r["still_crossed"] for r in rows]
    if not rows or len(set(labels)) < 2:
        out["not_computable"] = "both outcomes are needed for an AUC"
        return out
    scores = {**model_scores(rows, "cumulative"), **window_scores(rows),
              "adjusted": np.array([r["adjusted"] for r in rows], dtype=float)}
    out["ranking"] = day_bootstrap(rows, scores, draws=draws)
    edges = [r["edge_pips"] for r in rows if r.get("edge_pips") is not None]
    out["realised_edge_pips"] = {"mean": float(np.mean(edges)), "median": float(np.median(edges)), "n": len(edges)}
    out["not_still_crossed"] = int(len(rows) - sum(labels))
    return out


def run(cloud: Path, *, now: datetime | None = None, draws: int = BOOTSTRAP_DRAWS) -> dict:
    """`now` is for tests only; the command line always uses the wall clock."""
    if (now or datetime.now(UTC)) < INTERIM_END:
        raise SystemExit(f"the C44 interim window is open until {INTERIM_END.isoformat()}; nothing is examined before then")
    archive = cloud / "calendar" / "archive"
    return {"framing": "C44 interim, pre-specified 2026-11-21; descriptive, no decision weight",
            "generated_at": datetime.now(UTC).isoformat(), "cloud_data": str(cloud),
            "I1_ranking": ranking(cloud / "recordings", draws=draws),
            "I2_latency": latency.compute(cloud / "predictions" / "live_predictions.jsonl",
                                          archive if archive.exists() else None, SAMPLE_START, INTERIM_END)}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="C44 interim on the cloud sample (descriptive).")
    parser.add_argument("--cloud-data", type=Path, default=DEFAULT_CLOUD)
    parser.add_argument("--out", type=Path, default=RESULTS)
    args = parser.parse_args(argv)
    np.seterr(all="ignore")
    report = run(args.cloud_data)
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
