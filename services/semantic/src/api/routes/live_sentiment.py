from __future__ import annotations

from api.dashboard_service import DashboardDataService

from ..schemas import ApiErrorCode
from ..schemas import ApiRequestContext
from ..schemas import ApiResponseEnvelope
from ..schemas import ApiRouteError
from ..schemas import parse_live_sentiment_request


class LiveSentimentRoute:
    def __init__(self, dashboard_data: DashboardDataService) -> None:
        """Initializes the live-sentiment route dependencies."""
        self._dashboard_data = dashboard_data

    async def handle(self, request: ApiRequestContext) -> ApiResponseEnvelope:
        """Returns the current live-sentiment snapshot for a pair/window."""
        query = parse_live_sentiment_request(request.query_params)
        snapshot = await self._dashboard_data.get_live_sentiment(
            pair=query.pair,
            window_minutes=query.window_minutes,
        )
        if snapshot is None:
            raise ApiRouteError(503, ApiErrorCode.SERVICE_UNAVAILABLE, "Live sentiment not available.")
        return ApiResponseEnvelope(status_code=200, payload=snapshot)