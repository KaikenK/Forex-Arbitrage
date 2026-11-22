"""Writes research/colab/volctl_pipeline.ipynb: C46 window30 volatility control (run_window30_volctl,
branch ui-updates) on the frozen GDELT sample stored in the first account's Drive (MyDrive/arbex).

  python -m research.colab.make_volctl_notebook <ui-updates commit>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "volctl_pipeline.ipynb"

MD = """# Arbex C46: window30 volatility control (exploratory)

Runs `research/run_window30_volctl.py` from branch `ui-updates` (pre-registered as C46 in that branch's
tracker) on the frozen GDELT sample, its dedup and FinBERT scores, the calendar store and HistData, all
already in **MyDrive/arbex** of the account that ran the original pipeline. Use that account.

Runtime > Run all. Approve the Drive pop-up yourself. Inputs are copied to the Colab machine first (the
Drive mount dropped during long reads before); the only thing written back to Drive is the result file."""

CELLS = [
    """# 1. Settings
BRANCH = "ui-updates"
COMMIT = "{commit}"
DRIVE_ROOT = "/content/drive/MyDrive/arbex\"""",
    """# 2. Drive, and a check that every input is there
from google.colab import drive
drive.mount("/content/drive")
import os, pathlib, shutil, subprocess
A = pathlib.Path(DRIVE_ROOT, "artifacts")
needed = [A / "gdelt_sample_freeze.json", A / "gdelt_gkg_shards", A / "archive_scores" / "dedup",
          A / "archive_scores" / "finbert", A / "calendar_events.parquet", pathlib.Path(DRIVE_ROOT, "cache", "histdata")]
missing = [str(p) for p in needed if not p.exists()]
print("missing:", missing or "none")
assert not missing, "this Drive does not hold the frozen sample: sign in with the account that ran the pipeline\"""",
    """# 3. Code at COMMIT
if not os.path.exists("/content/Forex-Arbitrage"):
    subprocess.run(["git", "clone", "--branch", BRANCH, "https://github.com/KaikenK/Forex-Arbitrage.git",
                    "/content/Forex-Arbitrage"], check=True)
os.chdir("/content/Forex-Arbitrage")
subprocess.run(["git", "fetch", "-q", "origin", BRANCH], check=True)
subprocess.run(["git", "checkout", "-q", COMMIT], check=True)
CODE_COMMIT = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
print("code commit:", CODE_COMMIT)""",
    """# 4. Dependencies
!pip -q install structlog yfinance pyarrow""",
    """# 5. Copy the inputs to the Colab disk (byte-identical; the freeze hash is checked on load)
L = pathlib.Path("/content/local")
(L / "artifacts" / "archive_scores").mkdir(parents=True, exist_ok=True)
(L / "cache").mkdir(parents=True, exist_ok=True)
for src, dst in [(A / "gdelt_sample_freeze.json", L / "artifacts" / "gdelt_sample_freeze.json"),
                 (A / "calendar_events.parquet", L / "artifacts" / "calendar_events.parquet")]:
    shutil.copy2(src, dst)
for src, dst in [(A / "gdelt_gkg_shards", L / "artifacts" / "gdelt_gkg_shards"),
                 (A / "archive_scores" / "dedup", L / "artifacts" / "archive_scores" / "dedup"),
                 (A / "archive_scores" / "finbert", L / "artifacts" / "archive_scores" / "finbert"),
                 (pathlib.Path(DRIVE_ROOT, "cache", "histdata"), L / "cache" / "histdata")]:
    if not dst.exists():
        shutil.copytree(src, dst)
if (A / "gdelt_gkg_daily_counts.json").exists():
    shutil.copy2(A / "gdelt_gkg_daily_counts.json", L / "artifacts" / "gdelt_gkg_daily_counts.json")
repo = pathlib.Path("research/artifacts")
for name in ("gdelt_sample_freeze.json", "calendar_events.parquet", "gdelt_gkg_shards", "archive_scores",
             "gdelt_gkg_daily_counts.json"):
    link, target = repo / name, L / "artifacts" / name
    if target.exists() and not link.exists():
        link.symlink_to(target, target_is_directory=target.is_dir())
os.environ["ARBEX_CACHE_DIR"] = str(L / "cache")
print({p.name: p.exists() for p in repo.iterdir() if p.is_symlink()})""",
    """# 6. The volatility control (C46)
!python -m research.run_window30_volctl""",
    """# 7. Save the result next to the other results on Drive (never overwrites)
import datetime, json
result = repo / "window30_volctl_results.json"
target = A / "window30_volctl_results.json"
if target.exists():
    target = A / f"window30_volctl_results_{datetime.datetime.utcnow():%Y%m%dT%H%M%S}.json"
shutil.copy2(result, target)
print("saved", target, "| code", CODE_COMMIT)
print(result.read_text())""",
]


def build(commit: str) -> dict:
    cells = [{"cell_type": "markdown", "metadata": {}, "source": MD}]
    cells += [{"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
               "source": src.replace("{commit}", commit)} for src in CELLS]
    return {"cells": cells, "metadata": {"colab": {"provenance": []},
                                         "kernelspec": {"name": "python3", "display_name": "Python 3"}},
            "nbformat": 4, "nbformat_minor": 0}


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(sys.argv[1]), indent=1), encoding="utf-8")
    print(f"wrote {OUT} pinned to ui-updates {sys.argv[1]}")
