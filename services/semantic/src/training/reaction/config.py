from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

DEFAULT_HASH_DIMENSION = 256
DEFAULT_NEIGHBOR_COUNT = 5
DEFAULT_TRAIN_RATIO = 0.7
DEFAULT_VALIDATION_RATIO = 0.15
DEFAULT_DIRECTION_NEUTRAL_THRESHOLD = 0.0005
DEFAULT_CALIBRATION_BINS = 10
DEFAULT_EMBEDDER_MODEL = "all-MiniLM-L6-v2"
DEFAULT_MODEL_FAMILY = "linear"
ENV_PREFIX = "REACTION_TRAINING_"


def default_metadata_fields() -> list[str]:
    return ["source", "event_category", "macro_event_type", "scheduled", "standardized_surprise"]


class ReactionTrainingConfig(BaseSettings):
    training_table_path: Path
    output_dir: Path
    model_family: Literal["knn", "linear"] = DEFAULT_MODEL_FAMILY
    hash_dimension: int = Field(default=DEFAULT_HASH_DIMENSION, gt=0)
    k_neighbors: int = Field(default=DEFAULT_NEIGHBOR_COUNT, gt=0)
    train_ratio: float = Field(default=DEFAULT_TRAIN_RATIO, gt=0, lt=1)
    validation_ratio: float = Field(default=DEFAULT_VALIDATION_RATIO, ge=0, lt=1)
    direction_neutral_threshold: float = Field(default=DEFAULT_DIRECTION_NEUTRAL_THRESHOLD, ge=0)
    calibration_bins: int = Field(default=DEFAULT_CALIBRATION_BINS, gt=0)
    metadata_fields: list[str] = Field(default_factory=default_metadata_fields)
    use_semantic_features: bool = False
    embedder_model: str = DEFAULT_EMBEDDER_MODEL
    # "balanced" re-weights classes to equal mass (the long-standing default); "none" fits
    # the empirical class mix. With few neutral rows, "balanced" over-predicts neutral.
    direction_class_weight: Literal["balanced", "none"] = "balanced"
    # "shared": one weight per text token for every pair (the long-standing behaviour).
    # "pair_group": text tokens are keyed by pair group (market.pair_assets.text_token_group),
    # so each group's text weights are learned from its own rows; structured, pair, horizon
    # and metadata tokens stay shared.
    text_token_scope: Literal["shared", "pair_group"] = "shared"

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")
