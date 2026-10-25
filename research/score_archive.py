"""Batch-score the consolidated GDELT archive for H1/H5: dedup, lexicon, FinBERT, embeddings.

Laptop-safe by design (RESEARCH_TRACKER §3): run it alone, never alongside the live model
service. torch is capped at 4 threads, batches are 32, and output is written in chunks of
`--chunk-size` headlines, each an atomic parquet (or .npy) part. A killed run resumes at the
first missing part. The manifest pins the archive (hash of the ordered event ids); if the
archive changes, the run refuses to mix old and new parts unless `--rebuild` is given.

Stages, in the order they should run (cheap first):
  dedup    global syndication dedup with the stateless hashing embedder -> canonical flag +
           coverage_count (H5 attributes each *deduplicated* headline once)
  lexicon  deterministic lexicon polarity (seconds)
  finbert  ProsusAI/finbert polarity (the strong baseline; the slow stage)
  embed    sentence-transformer embeddings for H1's novelty (all-MiniLM-L6-v2)

Sample (LOCKED PROTOCOL, §6 2026-10-25): "Scoring (lexicon, FinBERT, embeddings) runs after
the cutoff on the frozen list only." When `artifacts/gdelt_sample_freeze.json` exists the
archive is read from the frozen shards only (hash-verified), and each stage's manifest records
the freeze hash; `run_h1` / `run_h5` refuse scores whose manifest does not carry it. Without a
freeze the store is scored (useful for timing) but the output cannot feed H1/H5.

Run (after the cutoff and `python -m research.freeze_gdelt_sample`):
  python -m research.score_archive --stage dedup
  python -m research.score_archive --stage lexicon
  python -m research.score_archive --stage finbert
  python -m research.score_archive --stage embed
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
DEFAULT_STORE = ARTIFACTS / "events.parquet"
DEFAULT_OUT = ARTIFACTS / "archive_scores"
TORCH_THREADS = 4
BATCH_SIZE = 32
STAGES = ("dedup", "lexicon", "finbert", "embed")


ARCHIVE_COLUMNS = ["event_id", "timestamp", "headline", "domain", "currencies"]


def _archive(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame[ARCHIVE_COLUMNS].copy()
    frame["event_id"] = frame["event_id"].astype(str)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame.sort_values(["timestamp", "event_id"]).reset_index(drop=True)


def load_archive(store: Path = DEFAULT_STORE) -> tuple[pd.DataFrame, str | None]:
    """(archive, freeze sha256 or None). The frozen sample wins whenever it exists."""
    from research.freeze_gdelt_sample import frozen_events_or_none

    frozen = frozen_events_or_none()
    if frozen is not None:
        events, freeze = frozen
        return _archive(events), freeze["sha256"]
    return _archive(pd.read_parquet(store, columns=ARCHIVE_COLUMNS)), None


def require_frozen_scores(stage: str, freeze_sha: str, out_dir: Path = DEFAULT_OUT) -> None:
    """H1/H5 guard: the stage must have been scored on exactly this frozen sample."""
    manifest = out_dir / stage / "manifest.json"
    meta = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    if meta.get("freeze_sha256") != freeze_sha:
        raise SystemExit(f"archive_scores/{stage} was not scored on the frozen sample "
                         f"(manifest freeze {meta.get('freeze_sha256')!r}); re-run "
                         f"`python -m research.score_archive --stage {stage} --rebuild`")


def _archive_hash(frame: pd.DataFrame) -> str:
    return hashlib.sha256("\n".join(frame["event_id"].astype(str)).encode("utf-8")).hexdigest()[:16]


def _atomic(path: Path, write) -> None:
    temp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        write(temp)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _save_npy(path: Path, values: np.ndarray) -> None:
    # Through an explicit handle: np.save on a path appends ".npy" to the temp name, and the
    # handle must be closed before the rename on Windows.
    with path.open("wb") as handle:
        np.save(handle, values)


def _check_manifest(out_dir: Path, archive_hash: str, chunk_size: int, rebuild: bool,
                    freeze_sha: str | None = None) -> None:
    manifest = out_dir / "manifest.json"
    if manifest.exists():
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        same = (meta.get("archive_hash") == archive_hash and meta.get("chunk_size") == chunk_size
                and meta.get("freeze_sha256") == freeze_sha)
        if not same and not rebuild:
            raise SystemExit(f"{out_dir} was scored on a different archive or chunk size; "
                             "pass --rebuild to discard those parts")
        if not same:
            for part in out_dir.glob("part-*"):
                part.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    _atomic(manifest, lambda p: p.write_text(json.dumps(
        {"archive_hash": archive_hash, "chunk_size": chunk_size, "freeze_sha256": freeze_sha}),
        encoding="utf-8"))


def run_dedup(frame: pd.DataFrame, out_dir: Path) -> None:
    """Global dedup needs the whole archive at once (clusters cross chunk boundaries); the
    hashing embedder keeps that cheap. One output file."""
    from research.semantic.dedup import deduplicate
    from research.semantic.embeddings import HashingEmbedder

    vectors = HashingEmbedder().encode(frame["headline"].astype(str).tolist())
    canonical = deduplicate(
        frame["event_id"].astype(str).tolist(), [stamp.to_pydatetime() for stamp in frame["timestamp"]],
        frame["headline"].astype(str).tolist(), vectors, domains=frame["domain"].fillna("").tolist(),
    )
    coverage = {c.event_id: c.coverage_count for c in canonical}
    result = pd.DataFrame({
        "event_id": frame["event_id"].astype(str),
        "canonical": frame["event_id"].astype(str).isin(coverage),
        "coverage_count": frame["event_id"].astype(str).map(coverage),
    })
    _atomic(out_dir / "part-dedup.parquet", lambda p: result.to_parquet(p, index=False))
    print(f"  dedup: {len(frame)} -> {len(canonical)} canonical headlines")


def _scorer_for(stage: str):
    if stage == "lexicon":
        from research.semantic.sentiment import LexiconSentiment
        return LexiconSentiment()
    import torch
    torch.set_num_threads(TORCH_THREADS)
    if stage == "finbert":
        from research.semantic.sentiment import FinBertSentiment
        return FinBertSentiment(batch_size=BATCH_SIZE)
    from research.semantic.embeddings import SentenceTransformerEmbedder
    return SentenceTransformerEmbedder(batch_size=BATCH_SIZE)


def run_chunked(stage: str, frame: pd.DataFrame, out_dir: Path, chunk_size: int) -> None:
    worker = _scorer_for(stage)
    n_chunks = (len(frame) + chunk_size - 1) // chunk_size
    started = time.monotonic()
    done_now = 0
    for chunk in range(n_chunks):
        suffix = "npy" if stage == "embed" else "parquet"
        part = out_dir / f"part-{chunk:05d}.{suffix}"
        if part.exists():
            continue
        rows = frame.iloc[chunk * chunk_size:(chunk + 1) * chunk_size]
        texts = rows["headline"].astype(str).tolist()
        if stage == "embed":
            vectors = worker.encode(texts).astype(np.float32)
            _atomic(part, lambda p, v=vectors: _save_npy(p, v))
            ids = pd.DataFrame({"event_id": rows["event_id"].astype(str)})
            _atomic(out_dir / f"part-{chunk:05d}.ids.parquet", lambda p, d=ids: d.to_parquet(p, index=False))
        else:
            scores = worker.score_many(texts)
            result = pd.DataFrame({
                "event_id": rows["event_id"].astype(str),
                "polarity": [s.polarity for s in scores],
                "confidence": [s.confidence for s in scores],
            })
            _atomic(part, lambda p, r=result: r.to_parquet(p, index=False))
        done_now += 1
        rate = done_now / max(time.monotonic() - started, 1e-9)
        remaining = sum(1 for c in range(chunk + 1, n_chunks)
                        if not (out_dir / f"part-{c:05d}.{suffix}").exists())
        print(f"  {stage}: chunk {chunk + 1}/{n_chunks} | ETA {remaining / rate / 60:.1f} min", flush=True)


def load_scores(stage: str, out_dir: Path = DEFAULT_OUT) -> pd.DataFrame:
    """Concatenate a scored stage (lexicon / finbert / dedup) for the analyses."""
    parts = sorted((out_dir / stage).glob("part-*.parquet"))
    return pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True) if parts else pd.DataFrame()


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the GDELT archive in resumable chunks.")
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--chunk-size", type=int, default=2000)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()

    frame, freeze_sha = load_archive(args.store)
    if freeze_sha is None:
        print("WARNING: no frozen sample (gdelt_sample_freeze.json); scoring the live store. "
              "These scores cannot be used for H1/H5.", flush=True)
    out_dir = args.out / args.stage
    _check_manifest(out_dir, _archive_hash(frame), args.chunk_size, args.rebuild, freeze_sha)
    print(f"Scoring {len(frame)} headlines, stage={args.stage} -> {out_dir}", flush=True)
    if args.stage == "dedup":
        run_dedup(frame, out_dir)
    else:
        run_chunked(args.stage, frame, out_dir, args.chunk_size)
    print("Done.", flush=True)


if __name__ == "__main__":
    main()
