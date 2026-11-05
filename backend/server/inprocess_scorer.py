"""Score live opportunities inside the backend when the Redis scorer is absent (display only).

The dashboard's arbitrage stream normally arrives via Redis: the streamer publishes
`arbex.raw_opps`, the separate `backend.core.semantic_engine` process scores them and
publishes `arbex.scored_opps`, and `WebSocketManager` relays those. Without Redis the
dashboard stays empty. This bridge runs the same `SemanticEngine.process_raw_opp` in the
backend's event loop with a publisher that broadcasts straight to the WebSockets.

Display only: it never touches the opportunity recorder, the semantic snapshot or the
prediction log, and it reuses the backend's existing SentimentBridge / CurrencyBiasClient
(no extra news-feed connection, no extra analyses).

ARBEX_INPROCESS_SCORER: "auto" (default; on while Redis is not connected), "on", "off".
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)

DISPLAY_TAG = "display — shadow scoring"


def scorer_mode() -> str:
    mode = os.environ.get("ARBEX_INPROCESS_SCORER", "auto").strip().lower()
    return mode if mode in ("auto", "on", "off") else "auto"


def raw_message(opp) -> Dict[str, Any]:
    """Same payload the streamer publishes on `arbex.raw_opps`."""
    return {
        "event_id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "opportunity": opp.to_dict(),
        "raw_score": opp.estimated_profit_pips,
    }


def route_key(opp) -> Tuple[str, str, str, str]:
    data = opp.to_dict()
    return (str(data.get("type")), str(data.get("buy_source")), str(data.get("sell_source")),
            ",".join(str(s) for s in data.get("symbols") or []))


class InProcessScorer:
    """Callable registered with `MultiSourceStreamer.on_arbitrage` (after the recorder)."""

    def __init__(
        self,
        engine_factory: Callable[[Callable[[str, Dict[str, Any]], Awaitable[None]]], Any],
        broadcast: Callable[[Dict[str, Any]], Awaitable[None]],
        *,
        redis_connected: Callable[[], bool] = lambda: False,
        mode: Optional[str] = None,
        shadow_lookup: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    ) -> None:
        self._broadcast = broadcast
        # C43 shadow row of the same opportunity (by id), attached for the dashboard's breakdown.
        self._shadow_lookup = shadow_lookup
        self._redis_connected = redis_connected
        self._mode = mode or scorer_mode()
        self._engine = engine_factory(self._publish) if self._mode != "off" else None
        self._in_flight: Set[Tuple[str, str, str, str]] = set()
        self.scored = 0
        self.skipped_busy = 0

    def active(self) -> bool:
        if self._engine is None:
            return False
        return self._mode == "on" or not self._redis_connected()

    async def _publish(self, channel: str, message: Dict[str, Any]) -> None:
        if channel != "arbex.scored_opps":
            return
        reason = str(message.get("ranking_reason") or "")
        message = {**message, "ranking_reason": f"{DISPLAY_TAG} | {reason}" if reason else DISPLAY_TAG}
        if self._shadow_lookup is not None:
            shadow = self._shadow_lookup(str((message.get("opportunity") or {}).get("id") or ""))
            if shadow:
                message["shadow"] = shadow
        self.scored += 1
        await self._broadcast(message)

    async def _run(self, key, raw: Dict[str, Any]) -> None:
        try:
            await self._engine.process_raw_opp(raw)
        except Exception as e:          # display path: never disturbs detection
            logger.warning(f"In-process scorer failed: {e}")
        finally:
            self._in_flight.discard(key)

    def __call__(self, opp) -> Optional["asyncio.Task[None]"]:
        if not self.active():
            return None
        key = route_key(opp)
        if key in self._in_flight:          # one scoring task per route; a dropped update is fine
            self.skipped_busy += 1
            return None
        self._in_flight.add(key)
        return asyncio.get_running_loop().create_task(self._run(key, raw_message(opp)))


def dashboard_stats(streamer_stats: Dict[str, Any]) -> Dict[str, Any]:
    """`stats` payload for the dashboard's tick counter (useWebSocket: payload.data.ticks_processed)."""
    ticks = int(streamer_stats.get("ticks_processed") or 0)
    detected = int(streamer_stats.get("arbitrage_opportunities_detected") or 0)
    return {"data": {"ticks_processed": ticks,
                     "opportunities_detected": detected,
                     "detection_rate_pct": round(100.0 * detected / ticks, 4) if ticks else 0.0}}
