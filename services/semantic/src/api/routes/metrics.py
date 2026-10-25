from __future__ import annotations

from market.interfaces import MarketAnalysisService

from ..schemas import ApiRequestContext
from ..schemas import ApiResponseEnvelope
from ..schemas import MetricsResponse


class MetricsRoute:
    def __init__(self, service: MarketAnalysisService) -> None:
        """Initializes the metrics route with the market service."""
        self._service = service

    async def handle(self, request: ApiRequestContext) -> ApiResponseEnvelope:
        """Returns current model and pipeline metrics."""
        del request
        metrics = await self._service.get_metrics()
        return ApiResponseEnvelope(status_code=200, payload=MetricsResponse(metrics=metrics))
