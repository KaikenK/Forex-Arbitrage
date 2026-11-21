"""Writes research/colab/c45_pipeline.ipynb (C45 replication; open it in Colab from GitHub).

  python -m research.colab.make_c45_notebook
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "c45_pipeline.ipynb"

MD = """# Arbex C45: window30 T2 replication on 2025-05..2026-10 news

Pre-registered in `research/RESEARCH_TRACKER.md` §6 (C45, 2026-11-21) before any data for the period was
extracted. The extraction already ran in BigQuery (project `arbex-c45`, `research/sql/c45_gkg_extract.sql`).

**How to run:** Runtime > Run all. Two Google pop-ups ask for permission (Drive, then BigQuery); approve
them yourself. Cells 1-10 give the primary result (no GPU needed). Cell 11 (FinBERT, GPU) is optional and
only adds the reported-only T1/T3. Nothing here costs money: the project is a BigQuery sandbox."""

CELLS = [
    """# 1. Settings
PROJECT = "arbex-c45"
BRANCH = "semantic-microservice"
COMMIT = "{commit}"          # code pinned for the run (recorded in the outputs)
DRIVE_ROOT = "/content/drive/MyDrive/arbex_c45\"""",
    """# 2. Google Drive (keeps the shards, freeze, scores and HistData across a dropped session)
from google.colab import drive
drive.mount("/content/drive")
import os, pathlib
for sub in ("c45", "cache"):
    pathlib.Path(DRIVE_ROOT, sub).mkdir(parents=True, exist_ok=True)
print(os.listdir(DRIVE_ROOT))""",
    """# 3. Code at COMMIT
import subprocess
if not os.path.exists("/content/Forex-Arbitrage"):
    subprocess.run(["git", "clone", "--branch", BRANCH, "https://github.com/KaikenK/Forex-Arbitrage.git",
                    "/content/Forex-Arbitrage"], check=True)
os.chdir("/content/Forex-Arbitrage")
subprocess.run(["git", "fetch", "-q", "origin", BRANCH], check=True)
subprocess.run(["git", "checkout", "-q", COMMIT], check=True)
CODE_COMMIT = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
print("code commit:", CODE_COMMIT)""",
    """# 4. Dependencies (Colab already has pandas, numpy, scipy, torch, transformers, google-cloud-bigquery)
!pip -q install structlog yfinance pyarrow db-dtypes""",
    """# 5. C45 outputs and the price cache live on Drive
repo_c45 = pathlib.Path("research/artifacts/c45")
if repo_c45.exists() and not repo_c45.is_symlink():
    import shutil; shutil.rmtree(repo_c45)
if not repo_c45.exists():
    repo_c45.symlink_to(pathlib.Path(DRIVE_ROOT, "c45"), target_is_directory=True)
os.environ["ARBEX_CACHE_DIR"] = str(pathlib.Path(DRIVE_ROOT, "cache"))
print(os.environ["ARBEX_CACHE_DIR"], "->", repo_c45.resolve())""",
    """# 6. HistData M1 for the period: 2025 yearly file and January-October 2026 monthly files (owner approved)
!python -m research.fetch_histdata --pairs EUR/USD,GBP/USD,USD/JPY,USD/CHF --years 2025
!python -m research.fetch_histdata --pairs EUR/USD,GBP/USD,USD/JPY,USD/CHF --months 2026:1-10""",
    """# 7. BigQuery sign-in (the arbex-c45 account)
from google.colab import auth
auth.authenticate_user()""",
    """# 8. Ingest: daily counts + 18 monthly shards (reads the table with tabledata.list: no query quota)
!python -m research.run_c45 ingest --project {project}""".replace("{project}", "arbex-c45"),
    """# 9. Freeze (hash + missing days), written once
!python -m research.run_c45 freeze""",
    """# 10. Dedup, then the primary test (T2-clock)
!python -m research.run_c45 score --stage dedup
!python -m research.run_c45 test""",
    """# 11. Optional (GPU): FinBERT for the reported-only T1/T3, then the test again (T2 is unchanged)
import torch; print("GPU:", torch.cuda.is_available())
!python -m research.run_c45 score --stage finbert
!python -m research.run_c45 test""",
]


def build(commit: str) -> dict:
    cells = [{"cell_type": "markdown", "metadata": {}, "source": MD}]
    for src in CELLS:
        cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
                      "source": src.replace("{commit}", commit)})
    return {"cells": cells, "metadata": {"accelerator": "GPU", "colab": {"provenance": []},
                                         "kernelspec": {"name": "python3", "display_name": "Python 3"}},
            "nbformat": 4, "nbformat_minor": 0}


if __name__ == "__main__":
    import subprocess
    import sys
    commit = sys.argv[1] if len(sys.argv) > 1 else subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    OUT.write_text(json.dumps(build(commit), indent=1), encoding="utf-8")
    print(f"wrote {OUT} pinned to {commit}")
