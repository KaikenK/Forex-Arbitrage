"""Write research/colab/arbex_gkg_pipeline.ipynb (DEVIATION 4: text stages in Colab).

The notebook runs this repository's own code (cloned at a pinned commit) on a Colab machine:
GKG ingestion from BigQuery -> freeze (after the cutoff) -> score_archive stages on the GPU ->
H1 / H5 / H6 and RQ2-E2E through `research.reproduce`. Everything it writes lives under
Google Drive `MyDrive/arbex/`, so a dropped session resumes where it stopped.

  python research/colab/make_notebook.py
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "arbex_gkg_pipeline.ipynb"


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)}


def code(text: str) -> dict:
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
            "source": text.strip("\n").splitlines(keepends=True)}


CELLS = [
    md("""
# Arbex: GDELT GKG news pipeline (DEVIATION 4)

Runs the repository's own research code on a Colab machine. Pre-registration and every rule this
notebook follows: `research/RESEARCH_TRACKER.md` §6 (DEVIATION 4 and later entries).

**Before you start**
1. Runtime → Change runtime type → **T4 GPU**.
2. Upload `arbex_colab_inputs.zip` (made on the laptop by `research/colab/make_inputs.py`) to
   Google Drive at `MyDrive/arbex/`.
3. Run the cells in order. Cells marked **after the cutoff** refuse to run before
   2026-11-10 23:59 IST.

Nothing here costs money: the BigQuery project is a sandbox with no billing account.
"""),
    code("""
# 1. Settings
PROJECT = "arbex-gdelt"
BRANCH = "semantic-microservice"
COMMIT = ""            # optional: pin a commit hash for the final run (recorded in the outputs)
DRIVE_ROOT = "/content/drive/MyDrive/arbex"
"""),
    code("""
# 2. Google Drive (inputs + every output; survives a dropped session)
from google.colab import drive
drive.mount("/content/drive")
import os, pathlib
for sub in ("artifacts", "cache", "logs"):
    pathlib.Path(DRIVE_ROOT, sub).mkdir(parents=True, exist_ok=True)
print(os.listdir(DRIVE_ROOT))
"""),
    code("""
# 3. Code: this repository at BRANCH (or COMMIT)
import subprocess, os
if not os.path.exists("/content/Forex-Arbitrage"):
    subprocess.run(["git", "clone", "--branch", BRANCH, "https://github.com/KaikenK/Forex-Arbitrage.git",
                    "/content/Forex-Arbitrage"], check=True)
os.chdir("/content/Forex-Arbitrage")
if COMMIT:
    subprocess.run(["git", "checkout", COMMIT], check=True)
CODE_COMMIT = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
print("code commit:", CODE_COMMIT)
"""),
    code("""
# 4. Dependencies (Colab already has torch, transformers, pandas, google-cloud-bigquery)
!pip -q install sentence-transformers yfinance pyarrow structlog
import torch; print("GPU:", torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else "")
"""),
    code("""
# 5. Point the repo's artifacts and caches at Drive; unpack the laptop inputs once
import shutil, zipfile
repo_artifacts = pathlib.Path("research/artifacts")
drive_artifacts = pathlib.Path(DRIVE_ROOT, "artifacts")
if repo_artifacts.exists() and not repo_artifacts.is_symlink():
    for f in repo_artifacts.iterdir():                 # committed small results seed the Drive copy
        target = drive_artifacts / f.name
        if f.is_file() and not target.exists():
            shutil.copy2(f, target)
    shutil.rmtree(repo_artifacts)
if not repo_artifacts.exists():
    repo_artifacts.symlink_to(drive_artifacts, target_is_directory=True)
os.environ["ARBEX_CACHE_DIR"] = str(pathlib.Path(DRIVE_ROOT, "cache"))
inputs = pathlib.Path(DRIVE_ROOT, "arbex_colab_inputs.zip")
if inputs.exists() and not (drive_artifacts / "calendar_events.parquet").exists():
    with zipfile.ZipFile(inputs) as z:
        for member in z.namelist():
            if member.endswith("/"):
                continue
            if member.startswith("artifacts/"):          # artifacts/x -> Drive artifacts/x
                out = drive_artifacts / member.split("/", 1)[1]
            else:                                        # histdata/PAIR/x.zip -> cache/histdata/PAIR/x.zip
                out = pathlib.Path(DRIVE_ROOT, "cache", member)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(member))
print("calendar store:", (drive_artifacts / "calendar_events.parquet").exists(),
      "| HistData pairs:", sorted(os.listdir(pathlib.Path(DRIVE_ROOT, "cache", "histdata")))
      if pathlib.Path(DRIVE_ROOT, "cache", "histdata").exists() else "missing")
"""),
    code("""
# 6. BigQuery sign-in (your Google account; nothing is stored in the repo)
from google.colab import auth
auth.authenticate_user()
"""),
    md("""
## Ingest the GKG extraction (allowed before the cutoff)

One shard per month, 2018-01..2025-04, written to Drive. Resumable: finished months are skipped.
"""),
    code("""
# 7. GKG -> monthly shards + daily counts (DEVIATION 4 rules; research/gkg_ingest.py)
!python -m research.gkg_ingest --project {PROJECT} 2>&1 | tee -a {DRIVE_ROOT}/logs/gkg_ingest.log
"""),
    code("""
# 8. Coverage preview (counts only; nothing is frozen)
!python -m research.freeze_gdelt_sample --preview
"""),
    md("""
## After the cutoff (2026-11-10 23:59 IST)

The freeze refuses to run earlier. Scoring and the tests read only the frozen sample.
"""),
    code("""
# 9. Freeze the sample (once; never overwritten)
!python -m research.reproduce --run freeze
"""),
    code("""
# 10. Score the frozen headlines (GPU). Each stage resumes where it stopped.
for stage in ("score_dedup", "score_lexicon", "score_finbert", "score_embed"):
    !python -m research.reproduce --run {stage} 2>&1 | tee -a {DRIVE_ROOT}/logs/scoring.log
"""),
    code("""
# 11. Pre-registered tests (H1 decides on the sentence-transformer; H5, H6; 30-min window; RQ2-E2E)
for step in ("h1_primary", "h1_tfidf", "h1_hashing", "h1_decide", "h5_lexicon", "h5_finbert", "h5_decide",
             "h6", "window30", "rq2_e2e"):
    !python -m research.reproduce --run {step} 2>&1 | tee -a {DRIVE_ROOT}/logs/tests.log
"""),
    code("""
# 12. Record the code commit next to the results
import json, datetime
pathlib.Path(DRIVE_ROOT, "artifacts", "colab_run.json").write_text(json.dumps({
    "code_commit": CODE_COMMIT, "finished_at": datetime.datetime.utcnow().isoformat() + "Z",
    "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}, indent=2))
print(sorted(p.name for p in pathlib.Path(DRIVE_ROOT, "artifacts").glob("*_results.json")))
"""),
]

notebook = {"cells": CELLS, "metadata": {"accelerator": "GPU", "colab": {"provenance": []},
                                          "kernelspec": {"display_name": "Python 3", "name": "python3"}},
            "nbformat": 4, "nbformat_minor": 0}
OUT.write_text(json.dumps(notebook, indent=1), encoding="utf-8")
print(f"wrote {OUT} ({len(CELLS)} cells)")
