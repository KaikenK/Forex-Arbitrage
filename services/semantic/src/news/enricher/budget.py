from __future__ import annotations

import asyncio
import json
from pathlib import Path

from datetime import UTC
from datetime import datetime
from datetime import timedelta

import structlog

from ..metrics import av_budget_remaining

DATE_KEY = "date"
COUNT_KEY = "count"


def utc_today() -> str:
    return datetime.now(UTC).date().isoformat()


class AlphaVantageBudget:
    def __init__(self, daily_limit: int = 25, state_file: Path | None = None) -> None:
        """Initializes the persisted Alpha Vantage budget."""
        if state_file is None:
            raise ValueError("state_file is required.")
        self._daily_limit = daily_limit
        self._state_file = state_file
        self._lock = asyncio.Lock()
        self._logger = structlog.get_logger().bind(component=self.__class__.__name__)

    async def can_call(self) -> bool:
        """Returns whether the daily Alpha Vantage budget allows another call."""
        async with self._lock:
            state = self._load_state()
            self._update_metric(state)
            return int(state[COUNT_KEY]) < self._daily_limit

    async def record_call(self) -> None:
        """Records a single Alpha Vantage API call."""
        async with self._lock:
            state = self._load_state()
            state[COUNT_KEY] = int(state[COUNT_KEY]) + 1
            self._persist_state(state)
            self._update_metric(state)

    def get_budget_status(self) -> dict[str, int | str]:
        """Returns the current Alpha Vantage budget usage snapshot."""
        state = self._load_state()
        used = int(state[COUNT_KEY])
        remaining = max(0, self._daily_limit - used)
        resets_at = self._resets_at(str(state[DATE_KEY])).isoformat()
        self._update_metric(state)
        return {"used": used, "remaining": remaining, "resets_at": resets_at}

    def _load_state(self) -> dict[str, str | int]:
        current_date = utc_today()
        default_state: dict[str, str | int] = {DATE_KEY: current_date, COUNT_KEY: 0}

        if not self._state_file.exists():
            self._persist_state(default_state)
            return default_state

        raw_state = json.loads(self._state_file.read_text(encoding="utf-8"))
        stored_date = raw_state.get(DATE_KEY)
        stored_count = raw_state.get(COUNT_KEY)

        if stored_date != current_date:
            self._persist_state(default_state)
            return default_state

        if not isinstance(stored_date, str) or not isinstance(stored_count, int):
            raise ValueError("Alpha Vantage budget state is invalid.")

        return {DATE_KEY: stored_date, COUNT_KEY: stored_count}

    def _persist_state(self, state: dict[str, str | int]) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(state), encoding="utf-8")

    def _resets_at(self, current_date: str) -> datetime:
        return datetime.fromisoformat(current_date).replace(tzinfo=UTC) + timedelta(days=1)

    def _update_metric(self, state: dict[str, str | int]) -> None:
        remaining = max(0, self._daily_limit - int(state[COUNT_KEY]))
        av_budget_remaining.set(float(remaining))
