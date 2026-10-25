from __future__ import annotations

import structlog

from market.interfaces import MarketAnalysisService

from ..schemas import ApiRequestContext
from ..schemas import ApiResponseEnvelope
from ..schemas import HistoryResponse
from ..schemas import parse_history_query


class HistoryRoute:
    logger = structlog.get_logger().bind(component="HistoryRoute")

    def __init__(self, service: MarketAnalysisService) -> None:
        """Initializes the history route with the market service."""
        self._service = service

    async def handle(self, request: ApiRequestContext) -> ApiResponseEnvelope:
        """Returns recent article analyses for history inspection."""
        history_query = parse_history_query(request.query_params)
        history_items = await self._service.list_history(limit=history_query.limit, filters=history_query.to_filters())
        return ApiResponseEnvelope(status_code=200, payload=HistoryResponse(items=history_items))
