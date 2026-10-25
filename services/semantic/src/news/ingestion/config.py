from __future__ import annotations

from pydantic import Field
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

DEFAULT_BASE_URL = "https://finnhub.io/api/v1"
DEFAULT_GENERAL_NEWS_CATEGORY = "general"
DEFAULT_POLL_INTERVAL_SECONDS = 30
DEFAULT_MAX_RETRIES = 5
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_RATE_LIMIT_PER_MINUTE = 55
ENV_PREFIX = "FINNHUB_"


class FinnhubConfig(BaseSettings):
    api_key: SecretStr
    base_url: str = Field(default=DEFAULT_BASE_URL, min_length=1)
    general_news_category: str = Field(default=DEFAULT_GENERAL_NEWS_CATEGORY, min_length=1)
    company_news_symbols: list[str] = Field(default_factory=list)
    poll_interval_seconds: int = Field(default=DEFAULT_POLL_INTERVAL_SECONDS, ge=0)
    max_retries: int = Field(default=DEFAULT_MAX_RETRIES, ge=1, le=DEFAULT_MAX_RETRIES)
    timeout_seconds: int = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0)
    rate_limit_per_minute: int = Field(default=DEFAULT_RATE_LIMIT_PER_MINUTE, ge=1)

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")
