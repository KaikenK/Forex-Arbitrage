from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from training.data_sources import DatasetName
from training.data_sources import DatasetPurpose
from training.data_sources import DatasetSource
from training.dataset_manifest import DatasetManifest


def test_dataset_manifest_parses_and_resolves_paths(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    manifest_path = config_dir / "datasets.toml"
    manifest_path.write_text(
        """
[[datasets]]
name = "financial_phrasebank"
source = "hugging_face"
license = "research"
purpose = "sentiment"
local_cache_path = "../data/cache/financial_phrasebank"
snapshot_id = "snapshot-1"
huggingface_id = "financial_phrasebank"
subset = "sentences_allagree"
split = "train"
preprocessing_notes = "Normalize labels."
""".strip(),
        encoding="utf-8",
    )

    manifest = DatasetManifest.from_toml(manifest_path)
    entry = manifest.datasets[0]

    assert entry.name is DatasetName.FINANCIAL_PHRASEBANK
    assert entry.source is DatasetSource.HUGGING_FACE
    assert entry.purpose is DatasetPurpose.SENTIMENT
    assert entry.local_cache_path == (config_dir / "../data/cache/financial_phrasebank").resolve()
    assert entry.huggingface_id == "financial_phrasebank"
    assert entry.subset == "sentences_allagree"
    assert entry.split == "train"


def test_dataset_manifest_rejects_duplicate_dataset_names(tmp_path: Path) -> None:
    manifest_path = tmp_path / "datasets.toml"
    manifest_path.write_text(
        """
[[datasets]]
name = "financial_phrasebank"
source = "hugging_face"
license = "research"
purpose = "sentiment"
local_cache_path = "cache/one"
snapshot_id = "snapshot-1"
huggingface_id = "financial_phrasebank"

[[datasets]]
name = "financial_phrasebank"
source = "hugging_face"
license = "research"
purpose = "validation"
local_cache_path = "cache/two"
snapshot_id = "snapshot-2"
huggingface_id = "financial_phrasebank"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate dataset manifest entry"):
        DatasetManifest.from_toml(manifest_path)


def test_dataset_manifest_requires_huggingface_id_for_hugging_face_sources(tmp_path: Path) -> None:
    manifest_path = tmp_path / "datasets.toml"
    manifest_path.write_text(
        """
[[datasets]]
name = "fiqa_sentiment_classification"
source = "hugging_face"
license = "research"
purpose = "sentiment"
local_cache_path = "cache/fiqa"
snapshot_id = "snapshot-1"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires huggingface_id"):
        DatasetManifest.from_toml(manifest_path)


def test_dataset_manifest_requires_local_source_path_for_local_sources(tmp_path: Path) -> None:
    manifest_path = tmp_path / "datasets.toml"
    manifest_path.write_text(
        """
[[datasets]]
name = "forex_factory_economic_calendar"
source = "local_file"
license = "research"
purpose = "market-reaction"
local_cache_path = "cache/forex_factory"
snapshot_id = "snapshot-1"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires local_source_path"):
        DatasetManifest.from_toml(manifest_path)
