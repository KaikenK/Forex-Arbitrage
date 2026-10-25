from __future__ import annotations

from api.dashboard_service import DashboardDataService

from ..schemas import ApiRequestContext
from ..schemas import ApiResponseEnvelope


class EvaluationRoute:
    def __init__(self, dashboard_data: DashboardDataService) -> None:
        """Initializes the evaluation route dependencies."""
        self._dashboard_data = dashboard_data

    async def handle(self, request: ApiRequestContext) -> ApiResponseEnvelope:
        """Returns evaluation metrics for the active reaction artifact."""
        del request
        payload = self._dashboard_data.get_evaluation() or {"error": "No evaluation data available"}
        return ApiResponseEnvelope(status_code=200, payload=payload)