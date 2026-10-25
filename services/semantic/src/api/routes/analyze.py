from __future__ import annotations

import structlog

from market.interfaces import MarketAnalysisService

from ..schemas import AnalyzeArticleResponse
from ..schemas import ApiErrorCode
from ..schemas import ApiRequestContext
from ..schemas import ApiResponseEnvelope
from ..schemas import ApiRouteError
from ..schemas import parse_analyze_article_request


class AnalyzeRoute:
    logger = structlog.get_logger().bind(component="AnalyzeRoute")

    def __init__(self, service: MarketAnalysisService) -> None:
        """Initializes the analyze route with the market service."""
        self._service = service

    async def handle(self, request: ApiRequestContext) -> ApiResponseEnvelope:
        """Processes one article analysis request and returns JSON payload data."""
        try:
            parsed_request = parse_analyze_article_request(request.json_body)
            result = await self._service.analyze_article(parsed_request.to_article_input())
        except ValueError as exc:
            self.logger.warning("analyze_route_validation_error", exc_info=True)
            raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, str(exc)) from exc
        return ApiResponseEnvelope(status_code=200, payload=AnalyzeArticleResponse(result=result))
