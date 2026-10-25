from __future__ import annotations

from collections.abc import Callable

from market.interfaces import MarketAnalysisService

from ..schemas import ApiRequestContext
from ..schemas import ApiResponseEnvelope
from ..schemas import HealthResponse


class HealthRoute:
    def __init__(
        self,
        service: MarketAnalysisService,
        runtime: dict[str, bool] | None = None,
        service_info: Callable[[], dict[str, object]] | None = None,
    ) -> None:
        """Initializes the health route with the market service, runtime flags and
        an optional provider of the microservice block (uptime, served artifact)."""
        self._service = service
        self._runtime = runtime
        self._service_info = service_info

    async def handle(self, request: ApiRequestContext) -> ApiResponseEnvelope:
        """Returns the service health payload plus resolved runtime flags."""
        del request
        health = await self._service.get_health()
        return ApiResponseEnvelope(
            status_code=200,
            payload=HealthResponse(
                health=health,
                runtime=self._runtime,
                service=self._service_info() if self._service_info is not None else None,
            ),
        )
