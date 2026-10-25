from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any
from typing import cast

from ..data_sources import DatasetName
from ..dataset_manifest import DatasetManifest
from ..dataset_manifest import DatasetManifestEntry
from .config import SentimentTrainingConfig
from .evaluate import POSITIVE_LABEL
from .evaluate import NEGATIVE_LABEL
from .evaluate import NEUTRAL_LABEL
from .evaluate import SentimentEvaluationResult
from .evaluate import evaluate_sentiment_predictions

PHRASEBANK_TEXT_KEYS = ("sentence", "text")
FIQA_TEXT_KEYS = ("sentence", "text", "headline")
TWITTER_TEXT_KEYS = ("text", "tweet", "headline")
LABEL_KEYS = ("label", "sentiment", "sentiment_label")
SCORE_KEYS = ("score", "sentiment_score")


@dataclass(frozen=True)
class SentimentExample:
    text: str
    label: int
    dataset_name: DatasetName
    source_metadata: dict[str, str]


def load_sentiment_examples(config: SentimentTrainingConfig) -> list[SentimentExample]:
    """Loads and normalizes cached sentiment datasets from the manifest."""
    manifest = DatasetManifest.from_toml(config.manifest_path)
    selected_entries = _select_manifest_entries(manifest, config.dataset_names)
    examples: list[SentimentExample] = []

    for entry in selected_entries:
        records = _load_cached_records(entry.local_cache_path)
        examples.extend(_normalize_records(entry, records))

    if not examples:
        raise ValueError("No sentiment examples were loaded from the configured datasets.")
    return examples


def stratified_validation_split(
    examples: list[SentimentExample],
    validation_ratio: float,
) -> tuple[list[SentimentExample], list[SentimentExample]]:
    """Splits examples into train and validation sets with label stratification."""
    if not 0 < validation_ratio < 1:
        raise ValueError("validation_ratio must be between 0 and 1.")
    if len(examples) < 2:
        raise ValueError("At least two examples are required for validation splitting.")

    grouped_examples: dict[int, list[SentimentExample]] = {}
    for example in examples:
        grouped_examples.setdefault(example.label, []).append(example)

    training_examples: list[SentimentExample] = []
    validation_examples: list[SentimentExample] = []

    for grouped_label_examples in grouped_examples.values():
        validation_count = _validation_count(len(grouped_label_examples), validation_ratio)
        split_index = len(grouped_label_examples) - validation_count
        training_examples.extend(grouped_label_examples[:split_index])
        validation_examples.extend(grouped_label_examples[split_index:])

    if not training_examples or not validation_examples:
        raise ValueError("Stratified validation split produced an empty train or validation set.")

    return training_examples, validation_examples


def train_sentiment_model(config: SentimentTrainingConfig) -> tuple[Any, Any, SentimentEvaluationResult]:
    """Fine-tunes a finance-domain encoder for sentiment classification."""
    transformers_module = import_module("transformers")
    datasets_module = import_module("datasets")

    AutoModelForSequenceClassification = getattr(transformers_module, "AutoModelForSequenceClassification")
    AutoTokenizer = getattr(transformers_module, "AutoTokenizer")
    Trainer = getattr(transformers_module, "Trainer")
    TrainingArguments = getattr(transformers_module, "TrainingArguments")
    Dataset = getattr(datasets_module, "Dataset")

    tokenizer = AutoTokenizer.from_pretrained(config.model_name)
    model = AutoModelForSequenceClassification.from_pretrained(config.model_name, num_labels=3)

    examples = load_sentiment_examples(config)
    train_examples, validation_examples = stratified_validation_split(examples, config.validation_ratio)
    train_dataset = Dataset.from_list(_dataset_rows(train_examples))
    validation_dataset = Dataset.from_list(_dataset_rows(validation_examples))

    def tokenize_batch(batch: dict[str, list[str]]) -> dict[str, Any]:
        return tokenizer(
            batch["text"],
            truncation=True,
            padding="max_length",
            max_length=config.max_length,
        )

    tokenized_train_dataset = train_dataset.map(tokenize_batch, batched=True)
    tokenized_validation_dataset = validation_dataset.map(tokenize_batch, batched=True)

    training_arguments = TrainingArguments(
        output_dir=str(config.output_dir),
        learning_rate=config.learning_rate,
        per_device_train_batch_size=config.batch_size,
        per_device_eval_batch_size=config.batch_size,
        num_train_epochs=config.num_train_epochs,
        weight_decay=config.weight_decay,
        evaluation_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="macro_f1",
        greater_is_better=True,
    )

    def compute_metrics(eval_prediction: Any) -> dict[str, float]:
        label_ids = list(cast(list[int], eval_prediction.label_ids))
        probabilities = _logits_to_probabilities(eval_prediction.predictions)
        evaluation_result = evaluate_sentiment_predictions(label_ids, probabilities)
        return {
            "accuracy": evaluation_result.accuracy,
            "macro_f1": evaluation_result.macro_f1,
            "calibration_score": evaluation_result.calibration_score,
        }

    trainer = Trainer(
        model=model,
        args=training_arguments,
        train_dataset=tokenized_train_dataset,
        eval_dataset=tokenized_validation_dataset,
        tokenizer=tokenizer,
        compute_metrics=compute_metrics,
    )
    trainer.train()

    prediction_output = trainer.predict(tokenized_validation_dataset)
    metrics = evaluate_sentiment_predictions(
        list(cast(list[int], prediction_output.label_ids)),
        _logits_to_probabilities(prediction_output.predictions),
    )
    return model, tokenizer, metrics


def _select_manifest_entries(
    manifest: DatasetManifest,
    dataset_names: list[DatasetName],
) -> list[DatasetManifestEntry]:
    entry_map = {entry.name: entry for entry in manifest.datasets}
    selected_entries: list[DatasetManifestEntry] = []
    for dataset_name in dataset_names:
        entry = entry_map.get(dataset_name)
        if entry is None:
            raise ValueError(f"Dataset manifest is missing required dataset: {dataset_name.value}")
        selected_entries.append(entry)
    return selected_entries


def _load_cached_records(cache_path: Path) -> list[dict[str, object]]:
    datasets_module = import_module("datasets")
    load_from_disk = getattr(datasets_module, "load_from_disk")
    cached_dataset = load_from_disk(str(cache_path))
    if hasattr(cached_dataset, "to_list"):
        raw_records = cached_dataset.to_list()
    else:
        raw_records = list(cached_dataset)

    records: list[dict[str, object]] = []
    for raw_record in raw_records:
        if not isinstance(raw_record, dict):
            raise ValueError("Each cached sentiment dataset record must be a dictionary.")
        records.append(raw_record)
    return records


def _normalize_records(entry: DatasetManifestEntry, records: list[dict[str, object]]) -> list[SentimentExample]:
    examples: list[SentimentExample] = []
    for record in records:
        text = _extract_text(entry.name, record)
        label = _extract_label(entry.name, record)
        examples.append(
            SentimentExample(
                text=text,
                label=label,
                dataset_name=entry.name,
                source_metadata={
                    "dataset_name": entry.name.value,
                    "snapshot_id": entry.snapshot_id,
                },
            ),
        )
    return examples


def _extract_text(dataset_name: DatasetName, record: dict[str, object]) -> str:
    if dataset_name is DatasetName.FINANCIAL_PHRASEBANK:
        return _first_non_empty_string(record, PHRASEBANK_TEXT_KEYS)
    if dataset_name is DatasetName.FIQA_SENTIMENT_CLASSIFICATION:
        return _first_non_empty_string(record, FIQA_TEXT_KEYS)
    if dataset_name is DatasetName.TWITTER_FINANCIAL_NEWS_SENTIMENT:
        return _first_non_empty_string(record, TWITTER_TEXT_KEYS)
    raise ValueError(f"Unsupported sentiment dataset: {dataset_name.value}")


def _extract_label(dataset_name: DatasetName, record: dict[str, object]) -> int:
    direct_label = _first_present_value(record, LABEL_KEYS)
    score_value = _first_present_value(record, SCORE_KEYS)

    if dataset_name is DatasetName.FINANCIAL_PHRASEBANK:
        return _normalize_label(direct_label)
    if dataset_name is DatasetName.FIQA_SENTIMENT_CLASSIFICATION:
        if direct_label is not None:
            return _normalize_label(direct_label)
        if score_value is None:
            raise ValueError("FiQA records require a label or sentiment score.")
        return _normalize_score(score_value)
    if dataset_name is DatasetName.TWITTER_FINANCIAL_NEWS_SENTIMENT:
        return _normalize_label(direct_label)
    raise ValueError(f"Unsupported sentiment dataset: {dataset_name.value}")


def _normalize_label(raw_label: object | None) -> int:
    if isinstance(raw_label, bool):
        raise ValueError("Boolean labels are not supported.")
    if isinstance(raw_label, int):
        if raw_label in (NEGATIVE_LABEL, NEUTRAL_LABEL, POSITIVE_LABEL):
            return raw_label
        raise ValueError(f"Unsupported integer sentiment label: {raw_label}")
    if isinstance(raw_label, float) and raw_label.is_integer():
        return _normalize_label(int(raw_label))
    if isinstance(raw_label, str):
        normalized_label = raw_label.strip().casefold()
        if normalized_label in {"negative", "bearish"}:
            return NEGATIVE_LABEL
        if normalized_label in {"neutral", "mixed"}:
            return NEUTRAL_LABEL
        if normalized_label in {"positive", "bullish"}:
            return POSITIVE_LABEL
    raise ValueError(f"Unsupported sentiment label value: {raw_label!r}")


def _normalize_score(raw_score: object) -> int:
    if isinstance(raw_score, bool):
        raise ValueError("Boolean sentiment scores are not supported.")
    if not isinstance(raw_score, int | float):
        raise ValueError(f"Unsupported sentiment score value: {raw_score!r}")
    if raw_score < 0:
        return NEGATIVE_LABEL
    if raw_score > 0:
        return POSITIVE_LABEL
    return NEUTRAL_LABEL


def _first_non_empty_string(record: dict[str, object], keys: tuple[str, ...]) -> str:
    value = _first_present_value(record, keys)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Record is missing a valid text field from {keys!r}.")
    return value


def _first_present_value(record: dict[str, object], keys: tuple[str, ...]) -> object | None:
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return None


def _validation_count(group_size: int, validation_ratio: float) -> int:
    if group_size <= 1:
        return 0
    tentative_count = int(group_size * validation_ratio)
    return max(1, min(group_size - 1, tentative_count))


def _dataset_rows(examples: list[SentimentExample]) -> list[dict[str, object]]:
    return [{"text": example.text, "label": example.label} for example in examples]


def _logits_to_probabilities(logits: Any) -> list[list[float]]:
    probability_rows: list[list[float]] = []
    for logit_row in logits:
        if isinstance(logit_row, list):
            probability_rows.append(_softmax([float(value) for value in logit_row]))
            continue
        probability_rows.append(_softmax([float(value) for value in list(logit_row)]))
    return probability_rows


def _softmax(values: list[float]) -> list[float]:
    max_value = max(values)
    exponentials = [pow(2.718281828459045, value - max_value) for value in values]
    total = sum(exponentials)
    return [value / total for value in exponentials]
