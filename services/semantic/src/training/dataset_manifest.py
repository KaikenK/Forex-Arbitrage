from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import tomllib
from typing import TypeVar

from .data_sources import DatasetName
from .data_sources import DatasetPurpose
from .data_sources import DatasetSource

DATASETS_KEY = "datasets"
NAME_KEY = "name"
SOURCE_KEY = "source"
LICENSE_KEY = "license"
PURPOSE_KEY = "purpose"
LOCAL_CACHE_PATH_KEY = "local_cache_path"
SNAPSHOT_ID_KEY = "snapshot_id"
PREPROCESSING_NOTES_KEY = "preprocessing_notes"
HUGGINGFACE_ID_KEY = "huggingface_id"
SUBSET_KEY = "subset"
SPLIT_KEY = "split"
LOCAL_SOURCE_PATH_KEY = "local_source_path"
EnumT = TypeVar("EnumT", bound=Enum)


@dataclass(frozen=True)
class DatasetManifestEntry:
    name: DatasetName
    source: DatasetSource
    license: str
    purpose: DatasetPurpose
    local_cache_path: Path
    snapshot_id: str
    preprocessing_notes: str | None = None
    huggingface_id: str | None = None
    subset: str | None = None
    split: str | None = None
    local_source_path: Path | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, object], base_dir: Path) -> DatasetManifestEntry:
        """Builds a dataset manifest entry from a TOML dictionary."""
        entry = cls(
            name=_parse_enum(DatasetName, raw, NAME_KEY),
            source=_parse_enum(DatasetSource, raw, SOURCE_KEY),
            license=_parse_required_string(raw, LICENSE_KEY),
            purpose=_parse_enum(DatasetPurpose, raw, PURPOSE_KEY),
            local_cache_path=_resolve_path(_parse_required_string(raw, LOCAL_CACHE_PATH_KEY), base_dir),
            snapshot_id=_parse_required_string(raw, SNAPSHOT_ID_KEY),
            preprocessing_notes=_parse_optional_string(raw, PREPROCESSING_NOTES_KEY),
            huggingface_id=_parse_optional_string(raw, HUGGINGFACE_ID_KEY),
            subset=_parse_optional_string(raw, SUBSET_KEY),
            split=_parse_optional_string(raw, SPLIT_KEY),
            local_source_path=_parse_optional_path(raw, LOCAL_SOURCE_PATH_KEY, base_dir),
        )
        entry.validate()
        return entry

    def validate(self) -> None:
        """Validates that the dataset entry is internally consistent."""
        if self.source is DatasetSource.HUGGING_FACE and not self.huggingface_id:
            raise ValueError(f"{self.name.value} requires huggingface_id for a Hugging Face source.")
        if self.source is DatasetSource.LOCAL_FILE and self.local_source_path is None:
            raise ValueError(f"{self.name.value} requires local_source_path for a local file source.")


@dataclass(frozen=True)
class DatasetManifest:
    datasets: list[DatasetManifestEntry]

    @classmethod
    def from_toml(cls, path: Path) -> DatasetManifest:
        """Parses a dataset manifest TOML file."""
        raw_manifest = tomllib.loads(path.read_text(encoding="utf-8"))
        raw_datasets = raw_manifest.get(DATASETS_KEY)
        if not isinstance(raw_datasets, list):
            raise ValueError("Dataset manifest must define a [[datasets]] list.")

        entries: list[DatasetManifestEntry] = []
        seen_names: set[DatasetName] = set()
        for raw_entry in raw_datasets:
            if not isinstance(raw_entry, dict):
                raise ValueError("Each dataset manifest entry must be a table.")
            entry = DatasetManifestEntry.from_dict(raw_entry, path.parent)
            if entry.name in seen_names:
                raise ValueError(f"Duplicate dataset manifest entry for {entry.name.value}.")
            seen_names.add(entry.name)
            entries.append(entry)

        manifest = cls(entries)
        manifest.validate()
        return manifest

    def validate(self) -> None:
        """Validates the dataset manifest contents."""
        if not self.datasets:
            raise ValueError("Dataset manifest must contain at least one dataset entry.")
        for entry in self.datasets:
            entry.validate()


def _parse_enum(enum_type: type[EnumT], raw: dict[str, object], key: str) -> EnumT:
    value = _parse_required_string(raw, key)
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f"Unsupported value for {key}: {value}") from error


def _parse_required_string(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string.")
    return value


def _parse_optional_string(raw: dict[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string when provided.")
    return value


def _parse_optional_path(raw: dict[str, object], key: str, base_dir: Path) -> Path | None:
    value = _parse_optional_string(raw, key)
    if value is None:
        return None
    return _resolve_path(value, base_dir)


def _resolve_path(raw_path: str, base_dir: Path) -> Path:
    resolved_path = Path(raw_path).expanduser()
    if resolved_path.is_absolute():
        return resolved_path
    return (base_dir / resolved_path).resolve()
