"""SHA-256 of every artifact the paper cites, written to artifact_hashes.json.

Text files are hashed after normalising CRLF to LF, so the digest does not depend on a checkout's
line-ending setting. Reads committed files only.

    python -m research.paper.ieee.make_hashes           # writes artifact_hashes.json
    python -m research.paper.ieee.make_hashes --check   # exit 1 if a file differs from the record
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
OUTPUT = HERE / "artifact_hashes.json"
SOURCE_COMMIT = "639fcdf"

CITED = [
    "RESEARCH_TRACKER.md", "REPRODUCE.md",
    "artifacts/reaction_eval.json", "artifacts/arrival_latency.json",
    "artifacts/window30_results.json", "artifacts/c45_results.json", "artifacts/c45_release_clocks.json",
    "artifacts/h1_results.json", "artifacts/h5_results.json", "artifacts/h6_results.json",
    "artifacts/h6b_results.json", "artifacts/rq5_extension_results.json",
    "artifacts/c40_results.json", "artifacts/c43_results.json",
    "artifacts/live_predictions_score_v1.json", "artifacts/live_predictions_score_release_v20.json",
    "artifacts/live_predictions_score_release_v21.json",
    "artifacts/positive_control.json", "artifacts/h2h3.json", "artifacts/rq2_e2e.json",
    "artifacts/calendar_events.manifest.json",
]


def digest(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix in {".json", ".md", ".tex", ".py", ".txt", ".bib"}:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def build(root: Path = RESEARCH, files: list[str] = CITED) -> dict:
    return {"source_commit": SOURCE_COMMIT, "hashing": "sha256 of file bytes, CRLF normalised to LF",
            "files": {name: digest(root / name) for name in files}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    current = build()
    if args.check:
        recorded = json.loads(OUTPUT.read_text(encoding="utf-8"))["files"]
        bad = [n for n, h in current["files"].items() if recorded.get(n) != h]
        print("hashes match" if not bad else f"MISMATCH: {bad}")
        return 1 if bad else 0
    OUTPUT.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.name} ({len(current['files'])} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
