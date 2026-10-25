"""GET /currency-index — per-currency market bias and per-pair relative bias.

Served by `market/currency_bias_tracker.CurrencyBiasTracker` when SEMANTIC_CURRENCY_BIAS is
on (older alias: SEMANTIC_ENABLE_CURRENCY_INDEX). The formula is the pre-registered H6 signal
(`market/currency_bias_core.py`, byte-identical to the research copy). With the flag off no
provider is wired and the route answers 501, so callers detect the capability instead of
receiving made-up numbers.

Provider contract: `provider(query_params) -> dict` returning at least
  {"as_of": iso8601, "currencies": {"USD": {...}}, "pairs": {"EUR/USD": {...}},
   "market_component": float | None, "method": str}
"""
from __future__ import annotations

from collections.abc import Callable

from ..schemas import ApiErrorCode
from ..schemas import ApiRequestContext
from ..schemas import ApiResponseEnvelope
from ..schemas import ApiRouteError

CurrencyIndexProvider = Callable[[dict[str, str]], dict[str, object]]


class CurrencyIndexRoute:
    def __init__(self, provider: CurrencyIndexProvider | None = None) -> None:
        self._provider = provider

    async def handle(self, request: ApiRequestContext) -> ApiResponseEnvelope:
        if self._provider is None:
            raise ApiRouteError(
                501,
                ApiErrorCode.NOT_IMPLEMENTED,
                "Currency sentiment index is not enabled on this service "
                "(set SEMANTIC_CURRENCY_BIAS=1 and restart).",
            )
        return ApiResponseEnvelope(status_code=200, payload=self._provider(dict(request.query_params)))
