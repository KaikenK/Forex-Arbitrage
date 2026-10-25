"""GET /calendar/upcoming?currencies=USD,EUR&hours=24 — cached live economic calendar.

Served by `market/economic_calendar.EconomicCalendar` when SEMANTIC_CURRENCY_BIAS is on;
otherwise 501, like /currency-index. The handler only reads the in-memory cache, so it
never waits on the upstream source.
"""
from __future__ import annotations

from ..schemas import ApiErrorCode
from ..schemas import ApiRequestContext
from ..schemas import ApiResponseEnvelope
from ..schemas import ApiRouteError

MAX_HOURS = 24.0 * 7


class CalendarUpcomingRoute:
    def __init__(self, calendar=None) -> None:
        self._calendar = calendar

    async def handle(self, request: ApiRequestContext) -> ApiResponseEnvelope:
        if self._calendar is None:
            raise ApiRouteError(
                501,
                ApiErrorCode.NOT_IMPLEMENTED,
                "Economic calendar is not enabled on this service (set SEMANTIC_CURRENCY_BIAS=1 and restart).",
            )
        params = request.query_params
        raw_currencies = str(params.get("currencies") or "").strip()
        currencies = [c.strip().upper() for c in raw_currencies.split(",") if c.strip()] or None
        try:
            hours = float(params.get("hours") or 24.0)
        except ValueError as exc:
            raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, "hours must be a number.") from exc
        if not 0.0 < hours <= MAX_HOURS:
            raise ApiRouteError(400, ApiErrorCode.VALIDATION_ERROR, f"hours must be in (0, {MAX_HOURS:g}].")
        include_unknown = str(params.get("include_unknown") or "").lower() in {"1", "true", "yes"}
        return ApiResponseEnvelope(status_code=200, payload={
            "calendar": self._calendar.status(),
            "currencies": currencies,
            "hours": hours,
            "releases": self._calendar.upcoming(currencies, hours, include_unknown=include_unknown),
        })
