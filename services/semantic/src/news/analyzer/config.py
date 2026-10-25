from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

DEFAULT_RECENCY_WINDOW_SECONDS = 60
DEFAULT_ZSCORE_WINDOW = 1000
ENV_PREFIX = "ANALYZER_"


def default_source_weights() -> dict[str, float]:
    return {
        "finnhub": 0.85,
        "rss": 0.75,
        "reddit": 0.55,
        "alpha_vantage": 0.9,
    }


class AnalyzerConfig(BaseSettings):
    source_weights: dict[str, float] = Field(default_factory=default_source_weights)
    recency_window_seconds: int = Field(default=DEFAULT_RECENCY_WINDOW_SECONDS, ge=1)
    zscore_window: int = Field(default=DEFAULT_ZSCORE_WINDOW, ge=1)

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")
