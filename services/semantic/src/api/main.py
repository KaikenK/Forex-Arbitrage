from __future__ import annotations

from dataclasses import asdict
from dataclasses import is_dataclass
from datetime import datetime
from enum import Enum
import json
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any
from typing import Protocol
from typing import cast
from urllib.parse import parse_qsl

import structlog

from api.dashboard_service import DashboardDataService
from market.interfaces import MarketAnalysisService

from .routes.analyze import AnalyzeRoute
from .routes.calendar import CalendarUpcomingRoute
from .routes.correlation import CorrelationRoute
from .routes.currency_index import CurrencyIndexProvider
from .routes.currency_index import CurrencyIndexRoute
from .routes.evaluation import EvaluationRoute
from .routes.health import HealthRoute
from .routes.history import HistoryRoute
from .routes.live_sentiment import LiveSentimentRoute
from .routes.metrics import MetricsRoute
from .routes.sentiment_bias import SentimentBiasRoute
from .schemas import API_VERSION_PREFIX
from .schemas import ApiErrorCode
from .schemas import ApiRequestContext
from .schemas import ApiResponseEnvelope
from .schemas import ApiRouteError
from .schemas import ErrorResponse
from .schemas import HttpMethod
from .schemas import RoutePath

HTTP_SCOPE_TYPE = "http"
APPLICATION_JSON = "application/json"
CONTENT_TYPE_HEADER = "content-type"
CONTENT_TYPE_SEPARATOR = ";"
ASGIMessage = dict[str, Any]
ASGIReceive = Callable[[], Awaitable[ASGIMessage]]
ASGISend = Callable[[ASGIMessage], Awaitable[None]]


class RouteHandler(Protocol):
    async def handle(self, request: ApiRequestContext) -> ApiResponseEnvelope:
        """Handles one API route request."""


class ApiApplication:
    logger = structlog.get_logger().bind(component="ApiApplication")

    def __init__(
        self,
        service: MarketAnalysisService,
        dashboard_data: DashboardDataService | None = None,
        runtime: dict[str, bool] | None = None,
        service_info: Callable[[], dict[str, object]] | None = None,
        currency_index_provider: CurrencyIndexProvider | None = None,
        calendar: object | None = None,
    ) -> None:
        """Initializes the ASGI application for article analysis routes."""
        self._routes: dict[tuple[HttpMethod, str], RouteHandler] = {
            (HttpMethod.POST, RoutePath.ANALYZE.value): AnalyzeRoute(service),
            (HttpMethod.GET, RoutePath.HISTORY.value): HistoryRoute(service),
            (HttpMethod.GET, RoutePath.HEALTH.value): HealthRoute(service, runtime, service_info),
            (HttpMethod.GET, RoutePath.METRICS.value): MetricsRoute(service),
            (HttpMethod.GET, RoutePath.CURRENCY_INDEX.value): CurrencyIndexRoute(currency_index_provider),
            (HttpMethod.GET, RoutePath.CALENDAR_UPCOMING.value): CalendarUpcomingRoute(calendar),
        }
        if dashboard_data is not None:
            self._routes.update(
                {
                    (HttpMethod.GET, RoutePath.EVALUATION.value): EvaluationRoute(dashboard_data),
                    (HttpMethod.GET, RoutePath.CORRELATION.value): CorrelationRoute(dashboard_data),
                    (HttpMethod.GET, RoutePath.LIVE_SENTIMENT.value): LiveSentimentRoute(dashboard_data),
                    (HttpMethod.GET, RoutePath.SENTIMENT_BIAS.value): SentimentBiasRoute(dashboard_data),
                },
            )

    async def __call__(self, scope: dict[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        """Handles one ASGI HTTP request and emits a JSON response."""
        if scope.get("type") != HTTP_SCOPE_TYPE:
            return

        try:
            request = await _build_request_context(scope, receive)
            route = self._resolve_route(request.method, request.path)
            response = await route.handle(request)
        except ApiRouteError as exc:
            self.logger.warning("api_route_error", exc_info=True)
            response = ApiResponseEnvelope(
                status_code=exc.status_code,
                payload=ErrorResponse(code=exc.code, message=exc.message),
            )

        await _send_json_response(send, response)

    def _resolve_route(self, method: HttpMethod, path: str) -> RouteHandler:
        route = self._routes.get((method, strip_version_prefix(path)))
        if route is None:
            raise ApiRouteError(404, ApiErrorCode.NOT_FOUND, f"No route configured for {method.value} {path}.")
        return route


def strip_version_prefix(path: str) -> str:
    """'/v1/analyze' -> '/analyze'; unversioned paths pass through unchanged."""
    if path == API_VERSION_PREFIX:
        return "/"
    if path.startswith(API_VERSION_PREFIX + "/"):
        return path[len(API_VERSION_PREFIX):]
    return path


def build_app(
    service: MarketAnalysisService,
    dashboard_data: DashboardDataService | None = None,
    runtime: dict[str, bool] | None = None,
    service_info: Callable[[], dict[str, object]] | None = None,
    currency_index_provider: CurrencyIndexProvider | None = None,
    calendar: object | None = None,
) -> ApiApplication:
    """Builds the ASGI application around a market analysis service.

    ``runtime`` carries the resolved feature flags so /health can report them;
    clients otherwise have no way to tell whether RAG is actually enabled.
    ``service_info`` supplies the microservice block of /health (uptime, served
    artifact). ``currency_index_provider`` serves /currency-index and ``calendar``
    (an EconomicCalendar) serves /calendar/upcoming; without them those routes answer 501.
    """
    return ApiApplication(service, dashboard_data, runtime, service_info, currency_index_provider, calendar)


async def _build_request_context(scope: dict[str, Any], receive: ASGIReceive) -> ApiRequestContext:
    method_name = scope.get("method", HttpMethod.GET.value)
    try:
        method = HttpMethod(method_name)
    except ValueError as exc:
        raise ApiRouteError(404, ApiErrorCode.NOT_FOUND, f"Unsupported method: {method_name}.") from exc

    headers = _headers_dict(scope.get("headers", []))
    body = await _read_body(receive)
    json_body: object | None = None
    if body:
        content_type = headers.get(CONTENT_TYPE_HEADER, APPLICATION_JSON).split(CONTENT_TYPE_SEPARATOR, maxsplit=1)[0]
        if content_type != APPLICATION_JSON:
            raise ApiRouteError(415, ApiErrorCode.UNSUPPORTED_MEDIA_TYPE, "Only application/json requests are supported.")
        try:
            json_body = json.loads(body.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ApiRouteError(400, ApiErrorCode.BAD_JSON, "Request body must be valid UTF-8 JSON.") from exc
        except json.JSONDecodeError as exc:
            raise ApiRouteError(400, ApiErrorCode.BAD_JSON, "Request body must contain valid JSON.") from exc

    query_params = dict(parse_qsl(scope.get("query_string", b"").decode("utf-8"), keep_blank_values=True))
    return ApiRequestContext(
        method=method,
        path=str(scope.get("path", "")),
        query_params=query_params,
        json_body=json_body,
    )


async def _read_body(receive: ASGIReceive) -> bytes:
    body_chunks: list[bytes] = []
    more_body = True
    while more_body:
        message = await receive()
        body_chunks.append(message.get("body", b""))
        more_body = bool(message.get("more_body", False))
    return b"".join(body_chunks)


def _headers_dict(raw_headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    return {
        header_name.decode("latin1").lower(): header_value.decode("latin1")
        for header_name, header_value in raw_headers
    }


async def _send_json_response(send: ASGISend, response: ApiResponseEnvelope) -> None:
    encoded_body = json.dumps(_json_compatible(response.payload)).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": response.status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(encoded_body)).encode("ascii")),
                (b"access-control-allow-origin", b"*"),
            ],
        },
    )
    await send(
        {
            "type": "http.response.body",
            "body": encoded_body,
            "more_body": False,
        },
    )


def _json_compatible(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_compatible(asdict(cast(Any, value)))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    return value
