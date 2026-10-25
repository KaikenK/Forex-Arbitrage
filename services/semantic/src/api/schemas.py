from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from enum import Enum

from market.models import ArticleAnalysis
from market.schemas import ArticleInput
from market.schemas import ServiceHealth
from market.schemas import ServiceMetrics
from market.service import HistoryFilters
from news.models import SourceType

DEFAULT_HISTORY_LIMIT = 20
DEFAULT_CORRELATION_LIMIT = 50
MAX_CORRELATION_LIMIT = 100
DEFAULT_LIVE_SENTIMENT_PAIR = "USD/INR"
DEFAULT_LIVE_SENTIMENT_WINDOW_MINUTES = 30
DEFAULT_SENTIMENT_BIAS_INTERVAL_MINUTES = 60
DEFAULT_SENTIMENT_BIAS_HORIZON_MINUTES = 60


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"


class RoutePath(str, Enum):
    ANALYZE = "/analyze"
    HISTORY = "/history"
    HEALTH = "/health"
    METRICS = "/metrics"
    EVALUATION = "/evaluation"
    CORRELATION = "/correlation"
    LIVE_SENTIMENT = "/sentiment/live"
    SENTIMENT_BIAS = "/sentiment/bias"
    CURRENCY_INDEX = "/currency-index"
    CALENDAR_UPCOMING = "/calendar/upcoming"


# Every route is also served under this prefix (/v1/analyze, /v1/health, ...). Clients
# should use the prefixed form; the bare paths stay for existing callers.
API_VERSION = "v1"
API_VERSION_PREFIX = f"/{API_VERSION}"


class ApiErrorCode(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    BAD_JSON = "BAD_JSON"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


@dataclass(frozen=True)
class ApiRequestContext:
    method: HttpMethod
    path: str
    query_params: dict[str, str]
    json_body: object | None


@dataclass(frozen=True)
class ApiResponseEnvelope:
    status_code: int
    payload: object


@dataclass(frozen=True)
class AnalyzeArticleRequest:
    article_text: str
    headline: str | None = None
    source: SourceType | None = None
    published_at: datetime | None = None
    external_id: str | None = None
    event_category: str | None = None
    macro_event_type: str | None = None
    currency_pair: str | None = None
    scheduled: bool | None = None
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    standardized_surprise: float | None = None
    calendar_source: str | None = None
    source_url: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    currency_pairs: list[str] | None = None

    def to_article_input(self) -> ArticleInput:
        """Converts the API request into a market service input."""
        return ArticleInput(
            article_text=self.article_text,
            headline=self.headline,
            source=self.source,
            published_at=self.published_at,
            external_id=self.external_id,
            event_category=self.event_category,
            macro_event_type=self.macro_event_type,
            currency_pair=self.currency_pair,
            scheduled=self.scheduled,
            actual=self.actual,
            forecast=self.forecast,
            previous=self.previous,
            standardized_surprise=self.standardized_surprise,
            calendar_source=self.calendar_source,
            source_url=self.source_url,
            metadata=dict(self.metadata),
            currency_pairs=list(self.currency_pairs) if self.currency_pairs else None,
        )


@dataclass(frozen=True)
class AnalyzeArticleResponse:
    result: ArticleAnalysis


@dataclass(frozen=True)
class HistoryResponse:
    items: list[ArticleAnalysis]


@dataclass(frozen=True)
class HistoryQuery:
    limit: int = DEFAULT_HISTORY_LIMIT
    pair: str | None = None
    source: str | None = None
    event_category: str | None = None
    text_query: str | None = None
    event_id: str | None = None
    has_evidence: bool | None = None
    min_confidence: float | None = None
    from_timestamp: datetime | None = None
    to_timestamp: datetime | None = None

    def to_filters(self) -> HistoryFilters:
        return HistoryFilters(
            pair=self.pair,
            source=self.source,
            event_category=self.event_category,
            text_query=self.text_query,
            event_id=self.event_id,
            has_evidence=self.has_evidence,
            min_confidence=self.min_confidence,
            from_timestamp=self.from_timestamp,
            to_timestamp=self.to_timestamp,
        )


@dataclass(frozen=True)
class HealthResponse:
    health: ServiceHealth
    # Resolved runtime feature flags (use_rag / use_sentence_transformer /
    # use_finbert). Clients cannot infer these: the server resolves them from
    # env-or-config.toml, so anything guessing from its own environment reports
    # the wrong state whenever config.toml is the source.
    runtime: dict[str, bool] | None = None
    # Microservice block: api_version, started_at, uptime_seconds, model_loaded and
    # the served artifact's identity, so a caller can tell *which* model answered.
    service: dict[str, object] | None = None


@dataclass(frozen=True)
class MetricsResponse:
    metrics: ServiceMetrics


@dataclass(frozen=True)
class CorrelationQuery:
    offset: int = 0
    limit: int = DEFAULT_CORRELATION_LIMIT


@dataclass(frozen=True)
class CorrelationResponse:
    total: int
    offset: int
    limit: int
    items: list[dict[str, object]]


@dataclass(frozen=True)
class LiveSentimentRequest:
    pair: str = DEFAULT_LIVE_SENTIMENT_PAIR
    window_minutes: int = DEFAULT_LIVE_SENTIMENT_WINDOW_MINUTES


@dataclass(frozen=True)
class SentimentBiasRequest:
    pair: str = DEFAULT_LIVE_SENTIMENT_PAIR
    from_timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    to_timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    aggregate_interval_minutes: int = DEFAULT_SENTIMENT_BIAS_INTERVAL_MINUTES
    horizon_minutes: int = DEFAULT_SENTIMENT_BIAS_HORIZON_MINUTES


@dataclass(frozen=True)
class ErrorResponse:
    code: ApiErrorCode
    message: str


class ApiRouteError(ValueError):
    def __init__(self, status_code: int, code: ApiErrorCode, message: str) -> None:
        """Initializes a structured API route error."""
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def parse_analyze_article_request(payload: object) -> AnalyzeArticleRequest:
    """Parses and validates the JSON body for article analysis."""
    if not isinstance(payload, dict):
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, "Request body must be a JSON object.")

    article_text = _required_string(payload, "article_text")
    headline = _optional_string(payload, "headline")
    external_id = _optional_string(payload, "external_id")
    event_category = _optional_string(payload, "event_category")
    macro_event_type = _optional_string(payload, "macro_event_type")
    currency_pair = _optional_string(payload, "currency_pair")
    scheduled = _optional_bool(payload, "scheduled")
    actual = _optional_float(payload, "actual")
    forecast = _optional_float(payload, "forecast")
    previous = _optional_float(payload, "previous")
    standardized_surprise = _optional_float(payload, "standardized_surprise")
    calendar_source = _optional_string(payload, "calendar_source")
    source_url = _optional_string(payload, "source_url")
    source = _optional_source(payload.get("source"))
    published_at = _optional_datetime(payload.get("published_at"))
    metadata = _metadata_dict(payload.get("metadata"))
    currency_pairs = _optional_pair_list(payload, "currency_pairs")

    return AnalyzeArticleRequest(
        article_text=article_text,
        headline=headline,
        source=source,
        published_at=published_at,
        external_id=external_id,
        event_category=event_category,
        macro_event_type=macro_event_type,
        currency_pair=currency_pair,
        scheduled=scheduled,
        actual=actual,
        forecast=forecast,
        previous=previous,
        standardized_surprise=standardized_surprise,
        calendar_source=calendar_source,
        source_url=source_url,
        metadata=metadata,
        currency_pairs=currency_pairs,
    )


# Each requested pair adds a linear score; cap the list so one request cannot fan out
# into an unbounded amount of work.
MAX_REQUESTED_PAIRS = 12


def _optional_pair_list(payload: dict[str, object], key: str) -> list[str] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, f"{key} must be a list of non-empty strings.")
    if len(value) > MAX_REQUESTED_PAIRS:
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, f"{key} accepts at most {MAX_REQUESTED_PAIRS} pairs.")
    return [item.strip().upper() for item in value]


def parse_history_query(query_params: dict[str, str]) -> HistoryQuery:
    """Parses history query parameters."""
    raw_limit = query_params.get("limit")
    if raw_limit is None:
        limit = DEFAULT_HISTORY_LIMIT
    else:
        try:
            limit = int(raw_limit)
        except ValueError as exc:
            raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, "limit must be an integer.") from exc
        if limit <= 0:
            raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, "limit must be greater than zero.")

    min_confidence = None
    raw_min_confidence = query_params.get("min_confidence")
    if raw_min_confidence is not None:
        try:
            min_confidence = float(raw_min_confidence)
        except ValueError as exc:
            raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, "min_confidence must be numeric.") from exc

    from_timestamp = _optional_query_datetime(query_params, "from_timestamp")
    to_timestamp = _optional_query_datetime(query_params, "to_timestamp")
    if from_timestamp is not None and to_timestamp is not None and to_timestamp < from_timestamp:
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, "to_timestamp must be later than from_timestamp.")

    return HistoryQuery(
        limit=limit,
        pair=_optional_query_string(query_params, "pair"),
        source=_optional_query_string(query_params, "source"),
        event_category=_optional_query_string(query_params, "event_category"),
        text_query=_optional_query_string(query_params, "q"),
        event_id=_optional_query_string(query_params, "event_id"),
        has_evidence=_optional_query_bool(query_params, "has_evidence"),
        min_confidence=min_confidence,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
    )


def parse_correlation_query(query_params: dict[str, str]) -> CorrelationQuery:
    """Parses correlation pagination parameters."""
    offset = _optional_query_int(query_params, "offset", default=0)
    limit = _optional_query_int(query_params, "limit", default=DEFAULT_CORRELATION_LIMIT)
    return CorrelationQuery(
        offset=max(0, offset),
        limit=min(MAX_CORRELATION_LIMIT, max(1, limit)),
    )


def parse_live_sentiment_request(query_params: dict[str, str]) -> LiveSentimentRequest:
    """Parses live-sentiment query parameters."""
    pair = query_params.get("pair", DEFAULT_LIVE_SENTIMENT_PAIR).strip() or DEFAULT_LIVE_SENTIMENT_PAIR
    window_minutes = _optional_query_int(
        query_params,
        "window_minutes",
        default=DEFAULT_LIVE_SENTIMENT_WINDOW_MINUTES,
    )
    if window_minutes <= 0:
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, "window_minutes must be greater than zero.")
    return LiveSentimentRequest(pair=pair, window_minutes=window_minutes)


def parse_sentiment_bias_request(query_params: dict[str, str]) -> SentimentBiasRequest:
    """Parses historical sentiment-bias query parameters."""
    pair = query_params.get("pair", DEFAULT_LIVE_SENTIMENT_PAIR).strip() or DEFAULT_LIVE_SENTIMENT_PAIR
    from_timestamp = _required_query_datetime(query_params, "from_timestamp")
    to_timestamp = _required_query_datetime(query_params, "to_timestamp")
    if to_timestamp <= from_timestamp:
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, "to_timestamp must be later than from_timestamp.")

    aggregate_interval_minutes = _optional_query_int(
        query_params,
        "aggregate_interval_minutes",
        default=DEFAULT_SENTIMENT_BIAS_INTERVAL_MINUTES,
    )
    if aggregate_interval_minutes <= 0:
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, "aggregate_interval_minutes must be greater than zero.")

    horizon_minutes = _optional_query_int(
        query_params,
        "horizon_minutes",
        default=DEFAULT_SENTIMENT_BIAS_HORIZON_MINUTES,
    )
    if horizon_minutes <= 0:
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, "horizon_minutes must be greater than zero.")

    return SentimentBiasRequest(
        pair=pair,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
        aggregate_interval_minutes=aggregate_interval_minutes,
        horizon_minutes=horizon_minutes,
    )


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, f"{key} must be a non-empty string.")
    return value.strip()


def _optional_string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, f"{key} must be a string.")
    stripped_value = value.strip()
    return stripped_value or None


def _optional_query_string(query_params: dict[str, str], key: str) -> str | None:
    value = query_params.get(key)
    if value is None:
        return None
    stripped_value = value.strip()
    return stripped_value or None


def _optional_query_bool(query_params: dict[str, str], key: str) -> bool | None:
    value = query_params.get(key)
    if value is None:
        return None
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, f"{key} must be a boolean.")


def _optional_query_datetime(query_params: dict[str, str], key: str) -> datetime | None:
    raw_value = query_params.get(key)
    if raw_value is None or not raw_value.strip():
        return None
    return _required_query_datetime(query_params, key)


def _optional_bool(payload: dict[str, object], key: str) -> bool | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, f"{key} must be a boolean.")


def _optional_float(payload: dict[str, object], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, f"{key} must be numeric.")
    return float(value)


def _optional_source(value: object) -> SourceType | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, "source must be a string.")
    normalized_value = value.strip().upper()
    try:
        return SourceType(normalized_value)
    except ValueError as exc:
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, f"Unsupported source: {value}.") from exc


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, "published_at must be an ISO timestamp string.")
    normalized_value = value.strip().replace("Z", "+00:00")
    try:
        parsed_value = datetime.fromisoformat(normalized_value)
    except ValueError as exc:
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, "published_at must be a valid ISO timestamp.") from exc
    if parsed_value.tzinfo is None or parsed_value.utcoffset() is None:
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, "published_at must be timezone-aware.")
    return parsed_value.astimezone(UTC)


def _metadata_dict(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, "metadata must be an object.")
    return {str(key): str(item) for key, item in value.items()}


def _optional_query_int(query_params: dict[str, str], key: str, *, default: int) -> int:
    raw_value = query_params.get(key)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, f"{key} must be an integer.") from exc


def _required_query_datetime(query_params: dict[str, str], key: str) -> datetime:
    raw_value = query_params.get(key)
    if raw_value is None or not raw_value.strip():
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, f"{key} is required.")
    normalized_value = raw_value.strip().replace("Z", "+00:00")
    try:
        parsed_value = datetime.fromisoformat(normalized_value)
    except ValueError as exc:
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, f"{key} must be a valid ISO timestamp.") from exc
    if parsed_value.tzinfo is None or parsed_value.utcoffset() is None:
        raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, f"{key} must be timezone-aware.")
    return parsed_value.astimezone(UTC)
