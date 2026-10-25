from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from ..data_sources import DatasetName

DEFAULT_MODEL_NAME = "ProsusAI/finbert"
DEFAULT_MAX_LENGTH = 256
DEFAULT_LEARNING_RATE = 2e-5
DEFAULT_BATCH_SIZE = 8
DEFAULT_NUM_TRAIN_EPOCHS = 3
DEFAULT_WEIGHT_DECAY = 0.01
DEFAULT_VALIDATION_RATIO = 0.2
DEFAULT_CALIBRATION_BINS = 10
ENV_PREFIX = "SENTIMENT_TRAINING_"


def default_dataset_names() -> list[DatasetName]:
    return [
        DatasetName.FINANCIAL_PHRASEBANK,
        DatasetName.FIQA_SENTIMENT_CLASSIFICATION,
        DatasetName.TWITTER_FINANCIAL_NEWS_SENTIMENT,
    ]


class SentimentTrainingConfig(BaseSettings):
    manifest_path: Path
    output_dir: Path
    model_name: str = Field(default=DEFAULT_MODEL_NAME, min_length=1)
    dataset_names: list[DatasetName] = Field(default_factory=default_dataset_names)
    max_length: int = Field(default=DEFAULT_MAX_LENGTH, gt=0)
    learning_rate: float = Field(default=DEFAULT_LEARNING_RATE, gt=0)
    batch_size: int = Field(default=DEFAULT_BATCH_SIZE, gt=0)
    num_train_epochs: int = Field(default=DEFAULT_NUM_TRAIN_EPOCHS, gt=0)
    weight_decay: float = Field(default=DEFAULT_WEIGHT_DECAY, ge=0)
    validation_ratio: float = Field(default=DEFAULT_VALIDATION_RATIO, gt=0, lt=1)
    calibration_bins: int = Field(default=DEFAULT_CALIBRATION_BINS, gt=0)

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")
