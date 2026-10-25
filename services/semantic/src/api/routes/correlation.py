from __future__ import annotations

from api.dashboard_service import DashboardDataService

from ..schemas import ApiRequestContext
from ..schemas import ApiResponseEnvelope
from ..schemas import parse_correlation_query


class CorrelationRoute:
    def __init__(self, dashboard_data: DashboardDataService) -> None:
        """Initializes the correlation route dependencies."""
        self._dashboard_data = dashboard_data

    async def handle(self, request: ApiRequestContext) -> ApiResponseEnvelope:
        """Returns one paginated slice of correlation rows."""
        query = parse_correlation_query(request.query_params)
        payload = self._dashboard_data.get_correlation_page(offset=query.offset, limit=query.limit)
        return ApiResponseEnvelope(status_code=200, payload=payload)