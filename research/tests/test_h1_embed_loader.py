"""The H1/H6 sentence-transformer loader keeps float32 and converts per stream; the encoded
matrices must equal the old whole-archive float64 conversion bit for bit. No network."""
import numpy as np
import pandas as pd
import pytest

from research import run_h1


def _write_parts(embed_dir, blocks, ids):
    embed_dir.mkdir(parents=True)
    start = 0
    for i, block in enumerate(blocks):
        np.save(embed_dir / f"part-{i:05d}.npy", block)
        pd.DataFrame({"event_id": ids[start:start + len(block)]}).to_parquet(
            embed_dir / f"part-{i:05d}.ids.parquet", index=False)
        start += len(block)


def test_loader_matches_old_float64_conversion(tmp_path, monkeypatch):
    rng = np.random.default_rng(7)
    blocks = [rng.standard_normal((n, 8)).astype(np.float32) for n in (5, 3, 4)]
    ids = [f"e{i}" for i in range(12)]
    _write_parts(tmp_path / "embed", blocks, ids)
    monkeypatch.setattr(run_h1, "SCORES", tmp_path)
    events = pd.DataFrame({"event_id": ids[:11], "headline": [f"headline {i}" for i in range(11)]})

    lookup = run_h1._embedder_factory("sentence_transformer", events)()
    texts = ["headline 9", "headline 0", "headline 4", "headline 10"]
    got = lookup.encode(texts)

    old_vectors = np.vstack(blocks).astype(np.float64)          # the code this replaces
    old = {f"headline {i}": old_vectors[i] for i in range(11)}
    expected = np.vstack([old[t] for t in texts])
    assert got.dtype == np.float64
    assert np.array_equal(got, expected)
    assert "headline 11" not in lookup.by_headline             # id without an event is skipped, as before


def test_loader_refuses_non_float32_parts(tmp_path, monkeypatch):
    _write_parts(tmp_path / "embed", [np.zeros((2, 4), dtype=np.float64)], ["a", "b"])
    monkeypatch.setattr(run_h1, "SCORES", tmp_path)
    events = pd.DataFrame({"event_id": ["a", "b"], "headline": ["x", "y"]})
    with pytest.raises(ValueError, match="expected float32"):
        run_h1._embedder_factory("sentence_transformer", events)
