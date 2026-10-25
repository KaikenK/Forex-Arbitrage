from __future__ import annotations

import sys
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from training.data_sources import DatasetName
from training.sentiment.config import DEFAULT_MODEL_NAME
from training.sentiment.config import SentimentTrainingConfig


def test_sentiment_training_config_parses_defaults(tmp_path: Path) -> None:
    config = SentimentTrainingConfig(
        manifest_path=tmp_path / "datasets.toml",
        output_dir=tmp_path / "artifacts",
    )

    assert config.model_name == DEFAULT_MODEL_NAME
    assert config.dataset_names == [
        DatasetName.FINANCIAL_PHRASEBANK,
        DatasetName.FIQA_SENTIMENT_CLASSIFICATION,
        DatasetName.TWITTER_FINANCIAL_NEWS_SENTIMENT,
    ]
    assert config.validation_ratio == 0.2
