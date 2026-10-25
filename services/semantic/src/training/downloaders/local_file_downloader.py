from __future__ import annotations

from pathlib import Path
import shutil

from ..data_sources import DatasetSource
from ..dataset_manifest import DatasetManifestEntry


class LocalFileDatasetDownloader:
    def download(self, entry: DatasetManifestEntry) -> Path:
        """Copies a local dataset snapshot into the configured cache path."""
        if entry.source is not DatasetSource.LOCAL_FILE:
            raise ValueError("LocalFileDatasetDownloader requires a local file manifest entry.")
        if entry.local_source_path is None:
            raise ValueError(f"{entry.name.value} is missing local_source_path.")
        if not entry.local_source_path.exists():
            raise FileNotFoundError(f"Local dataset source not found: {entry.local_source_path}")
        if entry.local_cache_path.exists():
            return entry.local_cache_path

        entry.local_cache_path.parent.mkdir(parents=True, exist_ok=True)
        if entry.local_source_path.is_dir():
            shutil.copytree(entry.local_source_path, entry.local_cache_path)
            return entry.local_cache_path

        if entry.local_cache_path.suffix:
            shutil.copy2(entry.local_source_path, entry.local_cache_path)
            return entry.local_cache_path

        entry.local_cache_path.mkdir(parents=True, exist_ok=True)
        cached_file_path = entry.local_cache_path / entry.local_source_path.name
        shutil.copy2(entry.local_source_path, cached_file_path)
        return cached_file_path
