from __future__ import annotations

from enum import Enum


class DatasetName(str, Enum):
    FINANCIAL_PHRASEBANK = "financial_phrasebank"
    FIQA_SENTIMENT_CLASSIFICATION = "fiqa_sentiment_classification"
    TWITTER_FINANCIAL_NEWS_SENTIMENT = "twitter_financial_news_sentiment"
    FOREX_FACTORY_ECONOMIC_CALENDAR = "forex_factory_economic_calendar"


class DatasetSource(str, Enum):
    HUGGING_FACE = "hugging_face"
    LOCAL_FILE = "local_file"


class DatasetPurpose(str, Enum):
    SENTIMENT = "sentiment"
    MARKET_REACTION = "market-reaction"
    RETRIEVAL = "retrieval"
    VALIDATION = "validation"
