from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta

from ..models import PriceBar


@dataclass(frozen=True)
class AlignedPriceWindow:
    start_bar: PriceBar
    end_bar: PriceBar
    horizon_minutes: int


class EventAlignment:
    def align(
        self,
        event_timestamp,
        price_bars: list[PriceBar],
        horizon_minutes: int,
    ) -> AlignedPriceWindow | None:
        """Aligns an event timestamp to a leak-safe future price window."""
        if horizon_minutes <= 0:
            raise ValueError("horizon_minutes must be positive.")
        if not price_bars:
            return None
        minimum_spacing = self._minimum_spacing_minutes(price_bars)
        if minimum_spacing is None:
            return None
        if horizon_minutes < minimum_spacing:
            return None

        target_timestamp = event_timestamp + timedelta(minutes=horizon_minutes)
        start_bar = self._first_bar_at_or_after(event_timestamp, price_bars)
        if start_bar is None:
            return None
        if not self._is_within_spacing_tolerance(
            expected_timestamp=event_timestamp,
            observed_timestamp=start_bar.timestamp,
            spacing_minutes=minimum_spacing,
        ):
            return None

        end_bar = self._first_bar_at_or_after(target_timestamp, price_bars)
        if end_bar is None:
            return None
        if not self._is_within_spacing_tolerance(
            expected_timestamp=target_timestamp,
            observed_timestamp=end_bar.timestamp,
            spacing_minutes=minimum_spacing,
        ):
            return None
        if end_bar.timestamp <= start_bar.timestamp:
            return None

        return AlignedPriceWindow(
            start_bar=start_bar,
            end_bar=end_bar,
            horizon_minutes=horizon_minutes,
        )

    def _first_bar_at_or_after(self, timestamp, price_bars: list[PriceBar]) -> PriceBar | None:
        for price_bar in price_bars:
            if price_bar.timestamp >= timestamp:
                return price_bar
        return None

    def _is_within_spacing_tolerance(
        self,
        *,
        expected_timestamp: datetime,
        observed_timestamp: datetime,
        spacing_minutes: int,
    ) -> bool:
        delay_minutes = (observed_timestamp - expected_timestamp).total_seconds() / 60
        return 0 <= delay_minutes <= spacing_minutes

    def _minimum_spacing_minutes(self, price_bars: list[PriceBar]) -> int | None:
        ordered_timestamps = sorted(price_bar.timestamp for price_bar in price_bars)
        positive_deltas: list[int] = []
        previous_timestamp: datetime | None = None
        for timestamp in ordered_timestamps:
            if previous_timestamp is None:
                previous_timestamp = timestamp
                continue

            delta_seconds = int((timestamp - previous_timestamp).total_seconds())
            previous_timestamp = timestamp
            if delta_seconds <= 0:
                continue

            delta_minutes = max(1, delta_seconds // 60)
            positive_deltas.append(delta_minutes)

        if not positive_deltas:
            return None
        return min(positive_deltas)
