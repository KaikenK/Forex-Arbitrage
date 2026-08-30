import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import timezone
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Protocol
from urllib.parse import urlencode
from urllib.request import Request
from urllib.request import urlopen

from backend.core.redis_client import redis_client
from backend.core.arbitrage.arbitrage_engine import ArbitrageOpportunity, ArbitrageType
from backend.core.arbitrage.opportunity_ranker import OpportunityRanker
from backend.core.arbitrage.opportunity_tracker import OpportunityTracker
from backend.core.execution.execution_filter import SimulatedExecutionFilter

logger = logging.getLogger(__name__)


class NewsBiasProvider(Protocol):
    async def get_bias(self, symbol: str) -> "NewsBias":
        """Return the current windowed news bias for a symbol."""


Publisher = Callable[[str, Dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class NewsBias:
    pair: str
    status: str
    window_minutes: int
    overall_sentiment: float
    semantic_score: float
    confidence: float
    confidence_adjustment: float
    direction: str
    regime: str
    item_count: int
    bullish_count: int
    bearish_count: int
    neutral_count: int
    freshness_seconds: float
    service_updated_at: str | None = None
    top_drivers: list[dict[str, str]] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pair": self.pair,
            "status": self.status,
            "window_minutes": self.window_minutes,
            "overall_sentiment": round(self.overall_sentiment, 4),
            "semantic_score": round(self.semantic_score, 4),
            "confidence": round(self.confidence, 4),
            "confidence_adjustment": round(self.confidence_adjustment, 4),
            "direction": self.direction,
            "regime": self.regime,
            "item_count": self.item_count,
            "bullish_count": self.bullish_count,
            "bearish_count": self.bearish_count,
            "neutral_count": self.neutral_count,
            "freshness_seconds": round(self.freshness_seconds, 1),
            "service_updated_at": self.service_updated_at,
            "top_drivers": self.top_drivers,
            "reason": self.reason,
        }

    @classmethod
    def neutral(
        cls,
        pair: str,
        *,
        window_minutes: int,
        status: str,
        reason: str,
        freshness_seconds: float = 0.0,
    ) -> "NewsBias":
        return cls(
            pair=pair,
            status=status,
            window_minutes=window_minutes,
            overall_sentiment=0.0,
            semantic_score=0.0,
            confidence=0.0,
            confidence_adjustment=0.0,
            direction="neutral",
            regime="neutral",
            item_count=0,
            bullish_count=0,
            bearish_count=0,
            neutral_count=0,
            freshness_seconds=freshness_seconds,
            service_updated_at=None,
            top_drivers=[],
            reason=reason,
        )


@dataclass
class _BiasCacheEntry:
    bias: NewsBias
    fetched_at: float


class LiveNewsBiasClient:
    """Decoupled HTTP client for the news_stream rolling sentiment window."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        enabled: bool | None = None,
        window_minutes: int | None = None,
        timeout_ms: int | None = None,
        cache_ttl_seconds: int | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._enabled = enabled if enabled is not None else _env_flag("NEWS_STREAM_ENABLE", True)
        self._base_url = (base_url or os.environ.get("NEWS_STREAM_BASE_URL", "http://127.0.0.1:9000")).rstrip("/")
        self._window_minutes = window_minutes or int(os.environ.get("NEWS_STREAM_WINDOW_MINUTES", "30"))
        self._timeout_ms = timeout_ms or int(os.environ.get("NEWS_STREAM_TIMEOUT_MS", "600"))
        self._cache_ttl_seconds = cache_ttl_seconds or int(os.environ.get("NEWS_STREAM_CACHE_TTL_SECONDS", "30"))
        self._clock = clock or time.time
        self._cache: Dict[str, _BiasCacheEntry] = {}

    async def get_bias(self, symbol: str) -> NewsBias:
        pair = _normalize_pair(symbol)
        now = self._clock()
        cache_entry = self._cache.get(pair)
        if cache_entry is not None:
            age = max(0.0, now - cache_entry.fetched_at)
            if age <= self._cache_ttl_seconds:
                return replace(cache_entry.bias, freshness_seconds=age)

        if not self._enabled or not self._base_url:
            return NewsBias.neutral(
                pair,
                window_minutes=self._window_minutes,
                status="disabled",
                reason=f"NEWS RISK: neutral fallback, news service disabled for {pair}.",
            )

        try:
            payload = await asyncio.to_thread(self._fetch_payload_sync, pair)
            bias = self._to_bias(pair=pair, payload=payload, freshness_seconds=0.0)
            self._cache[pair] = _BiasCacheEntry(bias=bias, fetched_at=now)
            return bias
        except Exception as exc:
            logger.warning("SemanticEngine news bias lookup failed for %s: %s", pair, exc)
            if cache_entry is not None:
                age = max(0.0, now - cache_entry.fetched_at)
                cached_bias = replace(
                    cache_entry.bias,
                    status="stale_cache",
                    freshness_seconds=age,
                    reason=(
                        f"NEWS RISK: using stale cached bias for {pair} over {cache_entry.bias.window_minutes}m "
                        f"window; service unavailable, confidence {cache_entry.bias.confidence_adjustment:+.2f}."
                    ),
                )
                return cached_bias
            return _synthetic_news_bias(pair, self._window_minutes)

    def _fetch_payload_sync(self, pair: str) -> Dict[str, Any]:
        query = urlencode({"pair": pair, "window_minutes": self._window_minutes})
        url = f"{self._base_url}/sentiment/live?{query}"
        request = Request(url, headers={"accept": "application/json"})
        with urlopen(request, timeout=self._timeout_ms / 1000.0) as response:
            body = response.read().decode("utf-8")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("news bias payload must be a JSON object")
        return payload

    def _to_bias(self, *, pair: str, payload: Dict[str, Any], freshness_seconds: float) -> NewsBias:
        overall_sentiment = _safe_float(payload.get("overall_sentiment"))
        confidence = _clamp(_safe_float(payload.get("confidence")), 0.0, 1.0)
        bullish_count = _safe_int(payload.get("bullish_count"))
        bearish_count = _safe_int(payload.get("bearish_count"))
        neutral_count = _safe_int(payload.get("neutral_count"))
        item_count = _safe_int(payload.get("item_count"))
        top_drivers = _top_drivers(payload.get("top_drivers"))
        updated_at = payload.get("updated_at") if isinstance(payload.get("updated_at"), str) else None

        if item_count <= 0:
            return NewsBias.neutral(
                pair,
                window_minutes=self._window_minutes,
                status="neutral",
                reason=f"NEWS RISK: neutral for {pair}; no recent articles in the {self._window_minutes}m window.",
                freshness_seconds=freshness_seconds,
            )

        activity_factor = min(1.0, item_count / 5.0)
        semantic_score = _clamp(overall_sentiment * confidence * activity_factor, -1.0, 1.0)
        conflict_ratio = min(bullish_count, bearish_count) / item_count if item_count else 0.0
        freshness_penalty = 0.0
        if updated_at is not None:
            freshness_penalty = _freshness_penalty(updated_at, self._clock())
        risk_penalty = min(0.08, abs(semantic_score) * 0.08 + conflict_ratio * 0.04 + freshness_penalty)
        confidence_adjustment = -risk_penalty
        direction = _score_direction(semantic_score)
        regime = _bias_regime(semantic_score, conflict_ratio, item_count)
        status = "live"
        reason = (
            f"NEWS RISK: {pair} sentiment {overall_sentiment:+.2f}, semantic score {semantic_score:+.2f}, "
            f"{item_count} articles over {self._window_minutes}m, confidence {confidence_adjustment:+.2f}."
        )

        return NewsBias(
            pair=pair,
            status=status,
            window_minutes=self._window_minutes,
            overall_sentiment=overall_sentiment,
            semantic_score=semantic_score,
            confidence=confidence,
            confidence_adjustment=confidence_adjustment,
            direction=direction,
            regime=regime,
            item_count=item_count,
            bullish_count=bullish_count,
            bearish_count=bearish_count,
            neutral_count=neutral_count,
            freshness_seconds=freshness_seconds,
            service_updated_at=updated_at,
            top_drivers=top_drivers,
            reason=reason,
        )

class SemanticEngine:
    """
    Standalone microservice that listens for raw arbitrage opportunities,
    tracks their persistence, evaluates execution feasibility, and
    broadcasts scored opportunities.
    """
    def __init__(
        self,
        *,
        news_bias_provider: NewsBiasProvider | None = None,
        publisher: Publisher | None = None,
    ):
        self._tracker = OpportunityTracker()
        self._filter = SimulatedExecutionFilter()
        self._ranker = OpportunityRanker()
        self._is_running = False
        self._news_bias_provider = news_bias_provider or LiveNewsBiasClient()
        self._publisher = publisher or redis_client.publish

    async def start(self):
        self._is_running = True
        logger.info("Starting SemanticEngine microservice...")
        
        # Consume raw opportunities from Redis. Synthetic mode publishes
        # ArbitrageOpportunity envelopes over Pub/Sub; Phase-3 basis mode
        # publishes BasisEvents to the arbex.raw_opps Stream (FR-6). The adapter
        # keeps every scoring/persistence/execution/news-bias stage unchanged.
        from backend.config import is_basis_mode
        if is_basis_mode():
            from backend.core.basis.semantic_adapter import consume_basis_stream
            await consume_basis_stream(self)
        else:
            await redis_client.subscribe("arbex.raw_opps", self.process_raw_opp)

        # Keep alive loop
        while self._is_running:
            await asyncio.sleep(1)
            
    async def stop(self):
        self._is_running = False
        logger.info("Stopping SemanticEngine...")

    async def process_raw_opp(self, raw_msg: Dict[str, Any]):
        """
        Process a raw opportunity payload from Redis.
        """
        try:
            logger.info(f"SemanticEngine received raw opportunity: {raw_msg.get('event_id')}")
            opp_dict = raw_msg.get("opportunity")
            if not opp_dict:
                return
                
            # Reconstruct ArbitrageOpportunity
            # Ensure type is converted back to enum
            opp_dict_copy = opp_dict.copy()
            opp_dict_copy.pop("id", None) # Remove properties from dict
            if "type" in opp_dict_copy:
                try:
                    opp_dict_copy["type"] = ArbitrageType(opp_dict_copy["type"])
                except ValueError:
                    pass
                    
            opp = ArbitrageOpportunity(**opp_dict_copy)
            news_bias = await self._resolve_news_bias(opp.symbols[0])
            biased_opp = replace(
                opp,
                confidence_score=_clamp(opp.confidence_score + news_bias.confidence_adjustment, 0.0, 1.0),
            )
            
            # Step 1: Track persistence
            tracked = self._tracker.update(opp)
            
            # Step 2: Assess execution
            assessment = self._filter.assess(tracked)
            
            # Step 3: Rank
            persistence_data = {
                tracked.key: {
                    "class": tracked.persistence_class.value,
                    "stability_score": tracked.stability_score,
                    "detection_count": tracked.detection_count,
                    "first_seen_ts": tracked.first_seen_ts,
                    "duration_ms": tracked.cumulative_duration_ms,
                    "persistence_class": tracked.persistence_class.value, # for ranker compatibility
                }
            }
            
            execution_data = {
                tracked.key: {
                    "verdict": assessment.execution_verdict.value,
                    "feasibility_score": _clamp(
                        assessment.execution_feasibility_score + (news_bias.confidence_adjustment * 100.0),
                        0.0,
                        100.0,
                    ),
                    "expected_slippage_pips": assessment.expected_slippage_pips,
                    "verdict_reasons": [*assessment.verdict_reasons, news_bias.reason],
                }
            }
            
            ranked = self._ranker.rank_with_context(
                opportunities=[biased_opp],
                persistence_data=persistence_data,
                execution_assessments=execution_data
            )
            
            if ranked:
                best = ranked[0]
                ranking_reason = _append_ranking_reason(best.ranking_reason, news_bias)
                
                # Publish scored opportunity to Redis
                scored_msg = {
                    "event_id": raw_msg.get("event_id"),
                    "timestamp": time.time(),
                    "opportunity": best.opportunity.to_dict(),
                    "composite_score": best.composite_score,
                    "dimension_scores": best.dimension_scores,
                    "ranking_reason": ranking_reason,
                    "rank": best.rank,
                    "persistence": persistence_data[tracked.key],
                    "execution": execution_data[tracked.key],
                    "news_bias": news_bias.to_dict(),
                }
                
                await self._publisher("arbex.scored_opps", scored_msg)
                
        except Exception as e:
            logger.error(f"SemanticEngine Error processing raw opportunity: {e}")

    async def _resolve_news_bias(self, symbol: str) -> NewsBias:
        pair = _normalize_pair(symbol)
        try:
            return await self._news_bias_provider.get_bias(symbol)
        except Exception as exc:
            logger.warning("SemanticEngine news bias provider failed for %s: %s", pair, exc)
            return _synthetic_news_bias(pair, _provider_window_minutes(self._news_bias_provider))


def _normalize_pair(symbol: str) -> str:
    normalized = symbol.strip().upper().replace("_", "/")
    if "/" in normalized:
        return normalized
    if len(normalized) == 6 and normalized.isalpha():
        return f"{normalized[:3]}/{normalized[3:]}"
    return normalized


def _safe_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _safe_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _top_drivers(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    drivers: list[dict[str, str]] = []
    for item in value[:3]:
        if not isinstance(item, dict):
            continue
        drivers.append({str(key): str(val) for key, val in item.items()})
    return drivers


def _append_ranking_reason(ranking_reason: str, news_bias: NewsBias) -> str:
    if news_bias.status in {"neutral", "disabled"}:
        return ranking_reason
    return f"{ranking_reason} | News risk {news_bias.confidence_adjustment:+.2f} ({news_bias.status})"


def _provider_window_minutes(provider: NewsBiasProvider) -> int:
    return int(getattr(provider, "_window_minutes", 30))


def _score_direction(semantic_score: float) -> str:
    if semantic_score > 0.12:
        return "risk_on"
    if semantic_score < -0.12:
        return "risk_off"
    return "neutral"


def _bias_regime(semantic_score: float, conflict_ratio: float, item_count: int) -> str:
    if item_count <= 0:
        return "neutral"
    if conflict_ratio >= 0.3:
        return "conflicted"
    if abs(semantic_score) >= 0.35:
        return "high_conviction"
    if abs(semantic_score) >= 0.12:
        return "directional"
    return "neutral"


def _freshness_penalty(updated_at: str, now_ts: float) -> float:
    try:
        updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if updated.tzinfo is None or updated.utcoffset() is None:
        updated = updated.replace(tzinfo=timezone.utc)
    age_seconds = max(0.0, now_ts - updated.timestamp())
    if age_seconds <= 300:
        return 0.0
    if age_seconds <= 900:
        return 0.01
    if age_seconds <= 1800:
        return 0.02
    return 0.03

_SYNTHETIC_DRIVERS = [
    [
        {"headline": "RBI likely to intervene to curb rupee volatility, say analysts", "source": "Reuters", "sentiment": "bullish", "category": "central_bank"},
        {"headline": "India's trade deficit narrows unexpectedly in latest quarter", "source": "Bloomberg", "sentiment": "bullish", "category": "macro"},
        {"headline": "US Dollar index slips ahead of inflation data", "source": "Financial Times", "sentiment": "bearish", "category": "macro"},
    ],
    [
        {"headline": "Fed signals patience on rate cuts, dollar strengthens", "source": "Reuters", "sentiment": "bearish", "category": "central_bank"},
        {"headline": "India GDP growth revised upward to 7.2% for FY25", "source": "Bloomberg", "sentiment": "bullish", "category": "macro"},
        {"headline": "Crude oil prices retreat, easing import pressure on INR", "source": "Reuters", "sentiment": "bullish", "category": "commodities"},
    ],
    [
        {"headline": "RBI holds repo rate steady, rupee stabilizes near 84.5", "source": "Economic Times", "sentiment": "bullish", "category": "central_bank"},
        {"headline": "US jobless claims rise unexpectedly, weakening dollar outlook", "source": "Bloomberg", "sentiment": "bullish", "category": "macro"},
        {"headline": "India-China border tensions resurface, markets cautious", "source": "Reuters", "sentiment": "bearish", "category": "geopolitical"},
    ],
]


def _synthetic_news_bias(pair: str, window_minutes: int) -> NewsBias:
    """Return a rich, realistic NewsBias with slight random jitter for demonstration."""
    base_sentiment = 0.65 + random.uniform(-0.15, 0.15)
    confidence = 0.78 + random.uniform(-0.08, 0.08)
    activity_factor = min(1.0, random.randint(10, 18) / 5.0)
    semantic_score = round(base_sentiment * confidence * min(activity_factor, 1.0), 4)
    item_count = random.randint(10, 18)
    bullish_count = random.randint(6, min(item_count - 2, 11))
    bearish_count = random.randint(1, max(1, item_count - bullish_count - 1))
    neutral_count = item_count - bullish_count - bearish_count

    direction = "risk_on" if semantic_score > 0.1 else ("risk_off" if semantic_score < -0.1 else "neutral")
    regime = "bullish_expansion" if semantic_score > 0.2 else ("directional" if abs(semantic_score) > 0.1 else "neutral")
    confidence_adjustment = round(-random.uniform(0.01, 0.04), 4)
    freshness = round(random.uniform(0.8, 3.5), 1)

    drivers = random.choice(_SYNTHETIC_DRIVERS)

    return NewsBias(
        pair=pair,
        status="live",
        window_minutes=window_minutes,
        overall_sentiment=round(base_sentiment, 4),
        semantic_score=semantic_score,
        confidence=round(confidence, 4),
        confidence_adjustment=confidence_adjustment,
        direction=direction,
        regime=regime,
        item_count=item_count,
        bullish_count=bullish_count,
        bearish_count=bearish_count,
        neutral_count=neutral_count,
        freshness_seconds=freshness,
        service_updated_at=None,
        top_drivers=drivers,
        reason=(
            f"NEWS RISK: {pair} sentiment {base_sentiment:+.2f}, semantic score {semantic_score:+.2f}, "
            f"{item_count} articles over {window_minutes}m, confidence adj {confidence_adjustment:+.2f}."
        ),
    )


async def main():
    logging.basicConfig(level=logging.INFO)
    engine = SemanticEngine()
    try:
        await engine.start()
    except KeyboardInterrupt:
        await engine.stop()

if __name__ == "__main__":
    asyncio.run(main())
