from __future__ import annotations

from api.dashboard_service import DashboardDataService

from ..schemas import ApiRequestContext
from ..schemas import ApiResponseEnvelope
from ..schemas import parse_sentiment_bias_request


class SentimentBiasRoute:
    def __init__(self, dashboard_data: DashboardDataService) -> None:
        """Initializes the sentiment-bias route dependencies."""
        self._dashboard_data = dashboard_data

    async def handle(self, request: ApiRequestContext) -> ApiResponseEnvelope:
        """Returns historical sentiment bias bins for a pair and time range."""
        query = parse_sentiment_bias_request(request.query_params)
        payload = self._dashboard_data.get_sentiment_bias(
            pair=query.pair,
            from_timestamp=query.from_timestamp,
            to_timestamp=query.to_timestamp,
            aggregate_interval_minutes=query.aggregate_interval_minutes,
            horizon_minutes=query.horizon_minutes,
        )
        return ApiResponseEnvelope(status_code=200, payload=payload)