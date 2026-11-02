"""Bundle the laptop-only inputs the Colab notebook needs into arbex_colab_inputs.zip.

  artifacts/calendar_events.parquet     the calendar store (not in git)
  histdata/<PAIR>/*.zip                 HistData M1 yearly files, 2018-2025

Upload the zip to Google Drive at MyDrive/arbex/. A SHA-256 manifest of every file goes into the
zip and next to it, so the Colab run can be tied to these exact bytes.

  python research/colab/make_inputs.py
"""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

RESEARCH = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "arbex_colab_inputs.zip"


def histdata_dir() -> Path:
    override = os.environ.get("ARBEX_CACHE_DIR")
    base = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "arbex"
    return base / "histdata"


def main() -> int:
    files = [(RESEARCH / "artifacts" / "calendar_events.parquet", "artifacts/calendar_events.parquet")]
    hist = histdata_dir()
    files += [(p, f"histdata/{p.relative_to(hist).as_posix()}") for p in sorted(hist.rglob("*.zip"))]
    missing = [str(src) for src, _ in files if not src.exists()]
    if missing:
        raise SystemExit(f"missing inputs: {missing}")
    manifest = {arc: hashlib.sha256(src.read_bytes()).hexdigest() for src, arc in files}
    with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_STORED) as z:
        for src, arc in files:
            z.write(src, arc)
        z.writestr("artifacts/colab_inputs_manifest.json", json.dumps(manifest, indent=1))
    OUT.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB, {len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
