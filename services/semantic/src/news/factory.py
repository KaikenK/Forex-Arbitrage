from __future__ import annotations

import asyncio
import tomllib
from importlib import import_module
from pathlib import Path
from typing import Any

from pydantic import SecretStr
import structlog

from .analyzer.config import AnalyzerConfig
from .analyzer.context_tagger import ContextTagger
from .analyzer.sentiment_analyzer import SentimentAnalyzer
from .enricher.alpha_vantage_enricher import AlphaVantageEnricher
from .enricher.budget import AlphaVantageBudget
from .enricher.finbert_enricher import FinBERTEnricher
from .enricher.sentiment_enricher import SentimentEnricher
from .interfaces import NewsIngester
from .interfaces import NewsNormalizer
from .ingestion.config import FinnhubConfig
from .ingestion.finnhub_ingester import FinnhubIngester
from .ingestion.official_feeds_config import OfficialFeedsConfig
from .ingestion.official_feeds_ingester import OfficialFeedsIngester
from .ingestion.rate_limiter import TokenBucketRateLimiter
from .ingestion.rss_config import RSSConfig
from .ingestion.rss_ingester import RSSIngester
from .models import SentimentSignal
from .models import SourceType
from .normalizer.finnhub_normalizer import FinnhubNormalizer
from .normalizer.official_feeds_normalizer import OfficialFeedsNormalizer
from .normalizer.reddit_normalizer import RedditNormalizer
from .normalizer.rss_normalizer import RSSNormalizer
from .pipeline import NewsPipeline
from .queue.in_memory_queue import InMemoryNewsQueue

ALPHA_VANTAGE_STATE_FILENAME = "alpha_vantage_budget.json"
MASKED_SECRET = "***"
SECRET_KEYS = {"api_key", "client_secret"}


def build_pipeline(config_path: Path) -> NewsPipeline:
    """Builds a news pipeline from a TOML configuration file."""
    logger = structlog.get_logger().bind(component="build_pipeline")
    config_data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    priority_symbols = list(config_data.get("watchlist", {}).get("symbols", []))

    ingesters = _build_ingesters(config_data)
    normalizers: dict[SourceType, NewsNormalizer] = {
        SourceType.FINNHUB: FinnhubNormalizer(priority_symbols),
        SourceType.RSS: RSSNormalizer(priority_symbols),
        SourceType.OFFICIAL: OfficialFeedsNormalizer(priority_symbols),
        SourceType.REDDIT: RedditNormalizer(priority_symbols),
    }

    alpha_vantage_section = config_data.get("alpha_vantage", {})
    av_budget = AlphaVantageBudget(
        state_file=config_path.parent / ALPHA_VANTAGE_STATE_FILENAME,
    )
    av_enricher = AlphaVantageEnricher(
        api_key=SecretStr(str(alpha_vantage_section.get("api_key", ""))),
        budget=av_budget,
    )
    finbert_enricher = FinBERTEnricher()
    sentiment_enricher = SentimentEnricher(
        av_enricher=av_enricher,
        finbert=finbert_enricher,
        priority_symbols=priority_symbols,
    )

    queue = InMemoryNewsQueue()
    analyzer = SentimentAnalyzer(AnalyzerConfig(**config_data.get("analyzer", {})), ContextTagger())
    output_queue: asyncio.Queue[SentimentSignal] = asyncio.Queue()
    dead_letter: asyncio.Queue[dict] = asyncio.Queue()

    if "zmq" in config_data:
        import_module("zmq")

    pipeline = NewsPipeline(
        ingesters=ingesters,
        normalizers=normalizers,
        enricher=sentiment_enricher,
        queue=queue,
        analyzer=analyzer,
        output_queue=output_queue,
        dead_letter=dead_letter,
    )
    pipeline._startup_config = _sanitize_config(config_data)
    logger.info("pipeline_config_loaded", config=pipeline._startup_config)
    return pipeline


def _build_ingesters(config_data: dict[str, Any]) -> list[NewsIngester]:
    ingesters: list[NewsIngester] = []

    if "finnhub" in config_data:
        finnhub_config = FinnhubConfig(**config_data["finnhub"])
        finnhub_rate_limiter = TokenBucketRateLimiter(finnhub_config.rate_limit_per_minute)
        ingesters.append(FinnhubIngester(finnhub_config, finnhub_rate_limiter))

    if "rss" in config_data:
        rss_config = RSSConfig(**config_data["rss"])
        ingesters.append(RSSIngester(rss_config))

    if "official_feeds" in config_data:
        official_feeds_config = OfficialFeedsConfig(**config_data["official_feeds"])
        ingesters.append(OfficialFeedsIngester(official_feeds_config))

    if "reddit" in config_data:
        import_module("praw")
        from .ingestion.reddit_config import RedditConfig
        from .ingestion.reddit_ingester import RedditIngester

        reddit_config = RedditConfig(**config_data["reddit"])
        ingesters.append(RedditIngester(reddit_config))

    return ingesters


def _sanitize_config(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: MASKED_SECRET if key in SECRET_KEYS else _sanitize_config(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_config(item) for item in value]
    return value
