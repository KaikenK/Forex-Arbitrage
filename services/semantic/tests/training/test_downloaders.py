from __future__ import annotations

import sys
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

import training.downloaders.huggingface_downloader as huggingface_downloader_module
from training.data_sources import DatasetName
from training.data_sources import DatasetPurpose
from training.data_sources import DatasetSource
from training.dataset_manifest import DatasetManifestEntry
from training.downloaders.huggingface_downloader import HuggingFaceDatasetDownloader
from training.downloaders.local_file_downloader import LocalFileDatasetDownloader


class FakeDataset:
    def __init__(self) -> None:
        self.saved_path: str | None = None

    def save_to_disk(self, path: str) -> None:
        self.saved_path = path


class FakeDatasetsModule:
    def __init__(self, dataset: FakeDataset) -> None:
        self.dataset = dataset
        self.calls: list[dict[str, str | None]] = []

    def load_dataset(
        self,
        dataset_id: str,
        subset: str | None = None,
        *,
        split: str | None = None,
        cache_dir: str | None = None,
    ) -> FakeDataset:
        self.calls.append(
            {
                "dataset_id": dataset_id,
                "subset": subset,
                "split": split,
                "cache_dir": cache_dir,
            },
        )
        return self.dataset


def test_huggingface_downloader_saves_dataset_to_configured_cache(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake_dataset = FakeDataset()
    fake_module = FakeDatasetsModule(fake_dataset)
    monkeypatch.setattr(
        huggingface_downloader_module,
        "import_module",
        lambda module_name: fake_module,
    )
    entry = DatasetManifestEntry(
        name=DatasetName.FINANCIAL_PHRASEBANK,
        source=DatasetSource.HUGGING_FACE,
        license="research",
        purpose=DatasetPurpose.SENTIMENT,
        local_cache_path=tmp_path / "cache" / "financial_phrasebank",
        snapshot_id="snapshot-1",
        huggingface_id="financial_phrasebank",
        subset="sentences_allagree",
        split="train",
    )

    cached_path = HuggingFaceDatasetDownloader().download(entry)

    assert cached_path == entry.local_cache_path
    assert fake_module.calls == [
        {
            "dataset_id": "financial_phrasebank",
            "subset": "sentences_allagree",
            "split": "train",
            "cache_dir": str(entry.local_cache_path.parent),
        },
    ]
    assert fake_dataset.saved_path == str(entry.local_cache_path)


def test_local_file_downloader_copies_source_into_cache(tmp_path: Path) -> None:
    source_path = tmp_path / "raw" / "calendar_snapshot.csv"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("date,currency,event\n", encoding="utf-8")
    entry = DatasetManifestEntry(
        name=DatasetName.FOREX_FACTORY_ECONOMIC_CALENDAR,
        source=DatasetSource.LOCAL_FILE,
        license="research",
        purpose=DatasetPurpose.MARKET_REACTION,
        local_cache_path=tmp_path / "cache" / "forex_factory",
        snapshot_id="snapshot-1",
        local_source_path=source_path,
    )

    cached_path = LocalFileDatasetDownloader().download(entry)

    assert cached_path == entry.local_cache_path / source_path.name
    assert cached_path.read_text(encoding="utf-8") == "date,currency,event\n"
