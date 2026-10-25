from __future__ import annotations

import sys
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

import training.sentiment.train as train_module
from training.sentiment.config import SentimentTrainingConfig
from training.sentiment.train import load_sentiment_examples
from training.sentiment.train import stratified_validation_split


class FakeDataset:
    def __init__(self, records: list[dict[str, object]]) -> None:
        self._records = records

    def to_list(self) -> list[dict[str, object]]:
        return list(self._records)


class FakeDatasetsModule:
    def __init__(self, dataset_by_path: dict[str, FakeDataset]) -> None:
        self._dataset_by_path = dataset_by_path

    def load_from_disk(self, path: str) -> FakeDataset:
        return self._dataset_by_path[path]


def test_load_sentiment_examples_normalizes_supported_datasets(
    monkeypatch,
    tmp_path: Path,
) -> None:
    phrasebank_path = (tmp_path / "cache" / "phrasebank").resolve()
    fiqa_path = (tmp_path / "cache" / "fiqa").resolve()
    twitter_path = (tmp_path / "cache" / "twitter").resolve()
    phrasebank_path_toml = phrasebank_path.as_posix()
    fiqa_path_toml = fiqa_path.as_posix()
    twitter_path_toml = twitter_path.as_posix()
    manifest_path = tmp_path / "datasets.toml"
    manifest_path.write_text(
        f"""
[[datasets]]
name = "financial_phrasebank"
source = "hugging_face"
license = "research"
purpose = "sentiment"
local_cache_path = "{phrasebank_path_toml}"
snapshot_id = "snapshot-1"
huggingface_id = "financial_phrasebank"

[[datasets]]
name = "fiqa_sentiment_classification"
source = "hugging_face"
license = "research"
purpose = "sentiment"
local_cache_path = "{fiqa_path_toml}"
snapshot_id = "snapshot-2"
huggingface_id = "fiqa_sentiment_classification"

[[datasets]]
name = "twitter_financial_news_sentiment"
source = "hugging_face"
license = "research"
purpose = "validation"
local_cache_path = "{twitter_path_toml}"
snapshot_id = "snapshot-3"
huggingface_id = "twitter_financial_news_sentiment"
""".strip(),
        encoding="utf-8",
    )

    fake_module = FakeDatasetsModule(
        {
            str(phrasebank_path): FakeDataset(
                [{"sentence": "Bank profits rise", "label": 2}],
            ),
            str(fiqa_path): FakeDataset(
                [{"sentence": "Macro outlook deteriorates", "sentiment_score": -0.7}],
            ),
            str(twitter_path): FakeDataset(
                [{"text": "Stocks steady as traders wait", "label": "neutral"}],
            ),
        },
    )
    monkeypatch.setattr(train_module, "import_module", lambda module_name: fake_module)

    config = SentimentTrainingConfig(
        manifest_path=manifest_path,
        output_dir=tmp_path / "artifacts",
    )
    examples = load_sentiment_examples(config)

    assert [example.text for example in examples] == [
        "Bank profits rise",
        "Macro outlook deteriorates",
        "Stocks steady as traders wait",
    ]
    assert [example.label for example in examples] == [2, 0, 1]
    assert [example.source_metadata["snapshot_id"] for example in examples] == [
        "snapshot-1",
        "snapshot-2",
        "snapshot-3",
    ]


def test_stratified_validation_split_preserves_each_label_bucket() -> None:
    examples = [
        train_module.SentimentExample(text=f"negative-{index}", label=0, dataset_name=train_module.DatasetName.FIQA_SENTIMENT_CLASSIFICATION, source_metadata={"dataset_name": "fiqa"})
        for index in range(3)
    ] + [
        train_module.SentimentExample(text=f"neutral-{index}", label=1, dataset_name=train_module.DatasetName.TWITTER_FINANCIAL_NEWS_SENTIMENT, source_metadata={"dataset_name": "twitter"})
        for index in range(3)
    ] + [
        train_module.SentimentExample(text=f"positive-{index}", label=2, dataset_name=train_module.DatasetName.FINANCIAL_PHRASEBANK, source_metadata={"dataset_name": "phrasebank"})
        for index in range(3)
    ]

    train_examples, validation_examples = stratified_validation_split(examples, validation_ratio=0.34)

    assert len(train_examples) == 6
    assert len(validation_examples) == 3
    assert {example.label for example in validation_examples} == {0, 1, 2}
