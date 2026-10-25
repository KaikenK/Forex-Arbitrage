from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import SentimentTrainingConfig
from .evaluate import SentimentEvaluationResult

ARTIFACT_METADATA_FILENAME = "artifact_metadata.json"


def export_inference_artifact(
    model: Any,
    tokenizer: Any,
    config: SentimentTrainingConfig,
    metrics: SentimentEvaluationResult | None = None,
) -> Path:
    """Exports a local sentiment inference artifact."""
    output_dir = config.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    model_save = getattr(model, "save_pretrained", None)
    tokenizer_save = getattr(tokenizer, "save_pretrained", None)
    if not callable(model_save):
        raise ValueError("model must provide save_pretrained().")
    if not callable(tokenizer_save):
        raise ValueError("tokenizer must provide save_pretrained().")

    model_save(str(output_dir))
    tokenizer_save(str(output_dir))

    metadata: dict[str, object] = {
        "model_name": config.model_name,
        "dataset_names": [dataset_name.value for dataset_name in config.dataset_names],
        "max_length": config.max_length,
        "batch_size": config.batch_size,
        "num_train_epochs": config.num_train_epochs,
    }
    if metrics is not None:
        metadata["metrics"] = {
            "accuracy": metrics.accuracy,
            "macro_f1": metrics.macro_f1,
            "confusion_matrix": metrics.confusion_matrix,
            "calibration_score": metrics.calibration_score,
        }

    (output_dir / ARTIFACT_METADATA_FILENAME).write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )
    return output_dir
