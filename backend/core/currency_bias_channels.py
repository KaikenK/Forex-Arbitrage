"""Two separate semantic channels for the opportunity ranker, behind ARBEX_CURRENCY_BIAS.

The per-currency market bias (semantic service /v1/currency-index, the H6 formula) is an
ADDITIONAL weighting factor for detected opportunities. It never creates, removes or
reverses an opportunity, and never acts as a trading signal on its own. Event risk is kept
apart from direction, because a currency can carry a strong directional bias while the
market is too jumpy to execute in:

  risk channel       expected volatility around news for the pair: the reaction model's
                     recent expected move (bps) and a high-impact release scheduled within
                     minutes or just out (the service's live calendar sets release_proximity
                     only inside its window: 15 min before, 10 min after). Penalty in confidence units:
                       min(RISK_CAP, RISK_SLOPE x max(0, expected_bps - RISK_FREE_BPS))
                       + RELEASE_PENALTY when release_proximity is known (capped as a whole).
  direction channel  the detector finds cross-venue crossed books, which are market-neutral
                     once both legs fill. The only directional exposure is legging: the
                     execution engine submits the BUY leg first, so if the second leg fails
                     the book is left long the pair. The penalty applies only when the pair
                     bias OPPOSES that residual exposure:
                       min(DIRECTION_CAP, DIRECTION_SLOPE x |relative bias| x confidence)
                     and is 0 when the bias is neutral, aligned, missing or its confidence
                     is below MIN_DIRECTION_CONFIDENCE. Opportunity types without a defined
                     legging exposure get 0.

Both channels only ever LOWER confidence, like the existing window-bias penalty (cap 0.08),
which stays in place unchanged. The constants are live-ranking engineering defaults, not
research results; H6b evaluates them prospectively on the recorder data.

With ARBEX_CURRENCY_BIAS off (default) nothing here is applied to scores; the channels are
still computed in shadow from cached values for the opportunity log, so H6b can compare
rankings with and without them.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

RISK_CAP = 0.06
RISK_FREE_BPS = 2.0
RISK_SLOPE = 0.004
RELEASE_PENALTY = 0.03
DIRECTION_CAP = 0.03
DIRECTION_SLOPE = 0.01
MIN_DIRECTION_CONFIDENCE = 0.3
CROSSED_BOOK_RESIDUAL = +1            # buy leg first (ExecutionEngine) -> residual long the pair
DEFAULT_CACHE_TTL_SECONDS = 30.0


def currency_bias_enabled() -> bool:
    raw = os.environ.get("ARBEX_CURRENCY_BIAS")
    return raw is not None and raw.strip().lower() not in {"", "0", "false", "no", "off"}


@dataclass(frozen=True)
class ChannelResult:
    name: str
    penalty: float                    # >= 0, subtracted from confidence
    reason: str
    inputs: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "penalty": round(self.penalty, 6), "reason": self.reason, "inputs": self.inputs}


def residual_exposure(opportunity_type: str) -> int:
    """+1 (long the pair) for a crossed book; 0 where no legging exposure is defined."""
    return CROSSED_BOOK_RESIDUAL if str(opportunity_type) == "cross_source" else 0


def risk_channel(pair_payload: Optional[Dict[str, Any]]) -> ChannelResult:
    if not pair_payload:
        return ChannelResult("risk", 0.0, "no currency-bias data", {})
    risk = pair_payload.get("event_risk") or {}
    expected = float(risk.get("expected_move_bps") or 0.0)
    penalty = RISK_SLOPE * max(0.0, expected - RISK_FREE_BPS)
    proximity = risk.get("release_proximity")
    if proximity is not None:
        penalty += RELEASE_PENALTY
    penalty = min(RISK_CAP, penalty)
    reason = f"expected move {expected:.1f} bps" + ("" if proximity is None else f", release {_describe(proximity)}")
    return ChannelResult("risk", penalty, reason, {"expected_move_bps": expected, "release_proximity": proximity})


def _describe(proximity: Any) -> str:
    """'in 4.0 min: USD Advance GDP q/q' / '2.0 min ago: ...'; other values as-is."""
    if not isinstance(proximity, dict):
        return str(proximity)
    minutes, event = proximity.get("minutes"), f"{proximity.get('currency', '')} {proximity.get('event', '')}".strip()
    if proximity.get("phase") == "pre_release":
        return f"in {minutes} min: {event}"
    if proximity.get("phase") == "post_release":
        return f"{minutes} min ago: {event}"
    return str(proximity)


def direction_channel(pair_payload: Optional[Dict[str, Any]], residual: int) -> ChannelResult:
    if not pair_payload or residual == 0:
        return ChannelResult("direction", 0.0, "no legging exposure" if residual == 0 else "no currency-bias data", {})
    bias = pair_payload.get("relative_bias")
    confidence = float(pair_payload.get("confidence") or 0.0)
    label = str(pair_payload.get("label") or "neutral")
    inputs = {"relative_bias": bias, "label": label, "confidence": confidence, "residual_exposure": residual}
    if bias is None or label == "neutral" or confidence < MIN_DIRECTION_CONFIDENCE:
        return ChannelResult("direction", 0.0, "bias neutral or low confidence", inputs)
    if (bias > 0) == (residual > 0):
        return ChannelResult("direction", 0.0, "bias aligned with residual exposure", inputs)
    penalty = min(DIRECTION_CAP, DIRECTION_SLOPE * abs(float(bias)) * confidence)
    return ChannelResult("direction", penalty, f"{label} bias opposes a {'long' if residual > 0 else 'short'} residual", inputs)


class CurrencyBiasClient:
    """Cached /currency-index lookups. `cached()` never touches the network."""

    def __init__(self, bridge, *, ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
                 clock: Callable[[], float] | None = None) -> None:
        self._bridge = bridge
        self._ttl = ttl_seconds
        self._clock = clock or time.time
        self._payload: Optional[Dict[str, Any]] = None
        self._fetched_at: Optional[float] = None
        self.status = "never_fetched"

    def refresh(self) -> Optional[Dict[str, Any]]:
        try:
            payload = self._bridge.fetch_currency_index()
        except Exception as exc:                     # service down: keep the last value
            logger.warning("currency index fetch failed: %s", exc)
            self.status = "unavailable"
            return self._payload
        self._payload = payload
        self._fetched_at = self._clock()
        self.status = "live" if payload is not None else "disabled_on_service"
        return payload

    async def pair(self, pair: str) -> Optional[Dict[str, Any]]:
        if self._fetched_at is None or self._clock() - self._fetched_at > self._ttl:
            await asyncio.to_thread(self.refresh)
        return self.cached(pair)

    def cached(self, pair: str) -> Optional[Dict[str, Any]]:
        if not self._payload:
            return None
        return (self._payload.get("pairs") or {}).get(pair)

    def age_seconds(self) -> Optional[float]:
        return None if self._fetched_at is None else self._clock() - self._fetched_at


def channel_snapshot(pair: str, opportunity_type: str, *, window_bias: Optional[Dict[str, Any]],
                     client: Optional[CurrencyBiasClient], applied: bool) -> Dict[str, Any]:
    """What the log records per opportunity: both channels + window bias, cached values only."""
    payload = client.cached(pair) if client is not None else None
    risk = risk_channel(payload)
    direction = direction_channel(payload, residual_exposure(opportunity_type))
    return {
        "snapshot_at": time.time(),
        "applied": applied,
        "currency_bias_status": client.status if client is not None else "no_client",
        "currency_bias_age_seconds": None if client is None else client.age_seconds(),
        "window_bias": window_bias,
        "currency_bias": None if payload is None else {
            k: payload.get(k) for k in ("relative_bias", "label", "confidence", "components")},
        "risk_channel": risk.to_dict() if payload is not None else None,
        "direction_channel": direction.to_dict() if payload is not None else None,
    }
