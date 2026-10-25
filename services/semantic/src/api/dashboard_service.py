from __future__ import annotations

from collections import defaultdict
from datetime import UTC
from datetime import datetime
from datetime import timedelta
import json
from pathlib import Path

from market.live_sentiment import LiveSentimentAggregator
from market.live_sentiment import LiveSentimentSnapshot

from .schemas import CorrelationResponse


class DashboardDataService:
    def __init__(
        self,
        *,
        evaluation_payload: dict[str, object] | None,
        training_table_path: Path | None,
        live_aggregator: LiveSentimentAggregator | None,
    ) -> None:
        """Initializes the dashboard data service dependencies."""
        self._evaluation_payload = evaluation_payload
        self._training_table_path = training_table_path
        self._live_aggregator = live_aggregator
        self._correlation_cache: list[dict[str, object]] | None = None

    def get_evaluation(self) -> dict[str, object] | None:
        """Returns the loaded evaluation payload when available."""
        return self._evaluation_payload

    async def get_live_sentiment(
        self,
        *,
        pair: str,
        window_minutes: int,
    ) -> LiveSentimentSnapshot | None:
        """Returns an aggregated live-sentiment snapshot when available."""
        if self._live_aggregator is None:
            return None
        return await self._live_aggregator.aggregate(pair=pair, window_minutes=window_minutes)

    def get_sentiment_bias(
        self,
        *,
        pair: str,
        from_timestamp: datetime,
        to_timestamp: datetime,
        aggregate_interval_minutes: int,
        horizon_minutes: int,
    ) -> dict[str, object]:
        """Returns historical bias bins over the training table for backtests."""
        rows = self._load_correlation_rows()
        normalized_pair = pair.upper()

        filtered_rows: list[tuple[datetime, dict[str, object]]] = []
        for row in rows:
            if str(row.get("pair", "")).upper() != normalized_pair:
                continue
            if _row_int(row, "horizon_minutes") != horizon_minutes:
                continue
            row_timestamp = _row_timestamp(row)
            if row_timestamp is None:
                continue
            if not (from_timestamp <= row_timestamp <= to_timestamp):
                continue
            filtered_rows.append((row_timestamp, row))

        if not filtered_rows:
            return {
                "pair": normalized_pair,
                "from_timestamp": from_timestamp,
                "to_timestamp": to_timestamp,
                "aggregate_interval_minutes": aggregate_interval_minutes,
                "horizon_minutes": horizon_minutes,
                "bins": [],
                "summary": {
                    "average_bias_score": 0.0,
                    "average_confidence": 0.0,
                    "average_reaction": 0.0,
                    "average_surprise": 0.0,
                    "total_items": 0,
                    "bullish_ratio": 0.0,
                    "bearish_ratio": 0.0,
                    "neutral_ratio": 0.0,
                },
            }

        bins: dict[datetime, list[dict[str, float | str]]] = defaultdict(list)
        for row_timestamp, row in filtered_rows:
            bin_timestamp = _bin_timestamp(row_timestamp, aggregate_interval_minutes)
            bins[bin_timestamp].append(_historical_bias_row(row))

        sorted_bins = sorted(bins.items(), key=lambda item: item[0])
        payload_bins: list[dict[str, object]] = []
        total_items = 0
        total_bullish = 0
        total_bearish = 0
        total_neutral = 0
        weighted_bias_sum = 0.0
        weighted_confidence_sum = 0.0
        total_weight = 0.0
        reaction_sum = 0.0
        surprise_sum = 0.0

        for bin_timestamp, entries in sorted_bins:
            item_count = len(entries)
            bullish_count = sum(1 for entry in entries if float(entry["bias_score"]) > 0.05)
            bearish_count = sum(1 for entry in entries if float(entry["bias_score"]) < -0.05)
            neutral_count = item_count - bullish_count - bearish_count

            bin_weight = sum(float(entry["confidence"]) for entry in entries)
            if bin_weight > 0:
                bias_score = sum(float(entry["bias_score"]) * float(entry["confidence"]) for entry in entries) / bin_weight
            else:
                bias_score = sum(float(entry["bias_score"]) for entry in entries) / item_count

            avg_confidence = sum(float(entry["confidence"]) for entry in entries) / item_count
            avg_reaction = sum(float(entry["reaction_label"]) for entry in entries) / item_count
            avg_surprise = sum(float(entry["standardized_surprise"]) for entry in entries) / item_count

            payload_bins.append(
                {
                    "timestamp": bin_timestamp,
                    "bias_score": round(bias_score, 4),
                    "confidence": round(avg_confidence, 4),
                    "average_reaction": round(avg_reaction, 6),
                    "average_surprise": round(avg_surprise, 4),
                    "bullish_count": bullish_count,
                    "bearish_count": bearish_count,
                    "neutral_count": neutral_count,
                    "item_count": item_count,
                },
            )

            total_items += item_count
            total_bullish += bullish_count
            total_bearish += bearish_count
            total_neutral += neutral_count
            weighted_bias_sum += sum(float(entry["bias_score"]) * float(entry["confidence"]) for entry in entries)
            weighted_confidence_sum += sum(float(entry["confidence"]) for entry in entries)
            total_weight += bin_weight
            reaction_sum += sum(float(entry["reaction_label"]) for entry in entries)
            surprise_sum += sum(float(entry["standardized_surprise"]) for entry in entries)

        average_bias_score = weighted_bias_sum / total_weight if total_weight > 0 else 0.0
        average_confidence = weighted_confidence_sum / total_items if total_items > 0 else 0.0
        average_reaction = reaction_sum / total_items if total_items > 0 else 0.0
        average_surprise = surprise_sum / total_items if total_items > 0 else 0.0

        return {
            "pair": normalized_pair,
            "from_timestamp": from_timestamp,
            "to_timestamp": to_timestamp,
            "aggregate_interval_minutes": aggregate_interval_minutes,
            "horizon_minutes": horizon_minutes,
            "bins": payload_bins,
            "summary": {
                "average_bias_score": round(average_bias_score, 4),
                "average_confidence": round(average_confidence, 4),
                "average_reaction": round(average_reaction, 6),
                "average_surprise": round(average_surprise, 4),
                "total_items": total_items,
                "bullish_ratio": round(total_bullish / total_items, 4) if total_items > 0 else 0.0,
                "bearish_ratio": round(total_bearish / total_items, 4) if total_items > 0 else 0.0,
                "neutral_ratio": round(total_neutral / total_items, 4) if total_items > 0 else 0.0,
            },
        }

    def get_correlation_page(self, *, offset: int, limit: int) -> CorrelationResponse:
        """Returns one paginated slice of correlation rows."""
        rows = self._load_correlation_rows()
        return CorrelationResponse(
            total=len(rows),
            offset=offset,
            limit=limit,
            items=rows[offset : offset + limit],
        )

    def _load_correlation_rows(self) -> list[dict[str, object]]:
        if self._correlation_cache is not None:
            return self._correlation_cache
        if self._training_table_path is None or not self._training_table_path.exists():
            self._correlation_cache = []
            return self._correlation_cache

        rows: list[dict[str, object]] = []
        for line in self._training_table_path.read_text(encoding="utf-8").splitlines():
            stripped_line = line.strip()
            if not stripped_line:
                continue
            try:
                raw_row = json.loads(stripped_line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw_row, dict):
                rows.append(raw_row)

        rows.sort(key=lambda row: str(row.get("timestamp", "")), reverse=True)
        self._correlation_cache = rows
        return self._correlation_cache


def _row_timestamp(row: dict[str, object]) -> datetime | None:
    raw_timestamp = row.get("timestamp")
    if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _row_float(row: dict[str, object], key: str) -> float:
    value = row.get(key)
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0.0
        try:
            return float(stripped)
        except ValueError:
            return 0.0
    return 0.0


def _row_int(row: dict[str, object], key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0
        try:
            return int(float(stripped))
        except ValueError:
            return 0
    return 0


def _row_bool(row: dict[str, object], key: str) -> bool:
    value = row.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return False


def _bin_timestamp(timestamp: datetime, interval_minutes: int) -> datetime:
    midnight = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_minutes = int((timestamp - midnight).total_seconds() // 60)
    bucket_minutes = (elapsed_minutes // interval_minutes) * interval_minutes
    return midnight + timedelta(minutes=bucket_minutes)


def _historical_bias_row(row: dict[str, object]) -> dict[str, float | str]:
    reaction_label = _row_float(row, "reaction_label")
    surprise_value = _row_float(row, "standardized_surprise")
    reaction_component = _clamp(reaction_label * 800.0, -1.0, 1.0)
    surprise_component = _clamp(surprise_value, -1.0, 1.0)
    scheduled_bonus = 0.1 if _row_bool(row, "scheduled") else 0.0
    confidence = _clamp(abs(reaction_component) * 0.6 + abs(surprise_component) * 0.25 + scheduled_bonus, 0.05, 1.0)
    bias_score = _clamp((reaction_component * 0.7) + (surprise_component * 0.3), -1.0, 1.0)
    return {
        "bias_score": bias_score,
        "confidence": confidence,
        "reaction_label": reaction_label,
        "standardized_surprise": surprise_value,
    }


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))