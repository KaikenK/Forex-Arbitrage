from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any

from ..data_sources import DatasetSource
from ..dataset_manifest import DatasetManifestEntry


class HuggingFaceDatasetDownloader:
    def download(self, entry: DatasetManifestEntry) -> Path:
        """Downloads a Hugging Face dataset into the configured cache path."""
        if entry.source is not DatasetSource.HUGGING_FACE:
            raise ValueError("HuggingFaceDatasetDownloader requires a Hugging Face manifest entry.")
        if entry.huggingface_id is None:
            raise ValueError(f"{entry.name.value} is missing huggingface_id.")
        if entry.local_cache_path.exists():
            return entry.local_cache_path

        entry.local_cache_path.parent.mkdir(parents=True, exist_ok=True)
        datasets_module = import_module("datasets")
        load_dataset = getattr(datasets_module, "load_dataset")
        dataset = load_dataset(
            entry.huggingface_id,
            entry.subset,
            split=entry.split,
            cache_dir=str(entry.local_cache_path.parent),
        )
        save_to_disk = getattr(dataset, "save_to_disk")
        save_to_disk(str(entry.local_cache_path))
        return entry.local_cache_path
