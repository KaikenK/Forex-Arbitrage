from __future__ import annotations

from pydantic import Field
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

DEFAULT_USER_AGENT = "news-pipeline/0.1"
DEFAULT_SUBREDDITS = ["wallstreetbets", "forex", "investing", "economics"]
DEFAULT_POST_LIMIT = 25
DEFAULT_POLL_INTERVAL_SECONDS = 120
DEFAULT_MIN_SCORE = 10
ENV_PREFIX = "REDDIT_"


class RedditConfig(BaseSettings):
    client_id: SecretStr
    client_secret: SecretStr
    user_agent: str = Field(default=DEFAULT_USER_AGENT, min_length=1)
    subreddits: list[str] = Field(default_factory=lambda: list(DEFAULT_SUBREDDITS))
    post_limit: int = Field(default=DEFAULT_POST_LIMIT, ge=1)
    poll_interval_seconds: int = Field(default=DEFAULT_POLL_INTERVAL_SECONDS, ge=0)
    min_score: int = Field(default=DEFAULT_MIN_SCORE)

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")
