"""
Bridge: BasisEvent stream  ->  the SemanticEngine's existing raw-opp pipeline.

The SemanticEngine consumes an ``{event_id, opportunity, raw_score}`` envelope
(a serialised ``ArbitrageOpportunity``) over Redis Pub/Sub in synthetic mode.
Phase 3 basis mode instead publishes ``BasisEvent`` records to the Redis
**Stream** ``arbex.raw_opps`` (FR-6). The two modes are mutually exclusive
(``config.DATA_MODE``), so this module lets the *same* SemanticEngine serve the
basis mode without changing any of its scoring / persistence / execution /
news-bias stages:

* ``basis_event_to_raw_msg`` maps a ``BasisEvent`` onto the envelope those stages
  already understand — ``basis_pips`` becomes ``estimated_profit_pips``, the two
  legs become ``buy_source`` / ``sell_source``, and everything basis-specific is
  carried through in ``details`` for the semantic owner to use as they wish.
* ``consume_basis_stream`` is the drop-in replacement for
  ``redis_client.subscribe("arbex.raw_opps", ...)`` when running in basis mode:
  it reads the Stream through a consumer group (so a slow/restarting engine never
  drops an event), adapts each record, and feeds it to ``engine.process_raw_opp``.

This is deliberately a self-contained module on the detection side of the frozen
schema boundary — wiring it in is a two-line branch in ``SemanticEngine.start``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

from backend.config import USDINR_PIP, BASIS_DETECTION_CONFIG
from backend.core.arbitrage.arbitrage_engine import ArbitrageOpportunity, ArbitrageType
from backend.core.basis.basis_event import BasisEvent, RAW_STREAM
from backend.core.basis.stream_consumer import BasisStreamConsumer, DEFAULT_GROUP
from backend.core.redis_client import redis_client

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

# BasisEvent.execution_verdict -> a 0..1 confidence the ranker/tracker expect
_VERDICT_CONFIDENCE = {
    "viable": 0.85,
    "risky": 0.55,
    "unlikely": 0.30,
    "unknown": 0.50,
}
_PERSISTENCE_CONFIDENCE_BONUS = {
    "ephemeral": 0.0,
    "flickering": 0.05,
    "persistent": 0.12,
}


def _session_for(ts: float) -> str:
    """Coarse session label from the IST wall clock (onshore hours ~09:00-17:00)."""
    hour = datetime.fromtimestamp(ts, tz=_IST).hour
    if hour < 12:
        return "TOKYO_LONDON"   # morning onshore = Asia/Europe overlap
    if hour < 18:
        return "LONDON"
    return "UNKNOWN"            # offshore-only hours


def _confidence_for(ev: BasisEvent) -> float:
    base = _VERDICT_CONFIDENCE.get(ev.execution_verdict, 0.5)
    base += _PERSISTENCE_CONFIDENCE_BONUS.get(ev.persistence_class, 0.0)
    return round(max(0.0, min(1.0, base)), 3)


def basis_event_to_raw_msg(ev: BasisEvent) -> Dict[str, Any]:
    """Map a BasisEvent onto the SemanticEngine's raw-opp envelope."""
    opp = ArbitrageOpportunity(
        type=ArbitrageType.CROSS_SOURCE,
        symbols=[ev.symbol],                       # "USDINR" -> news-bias lookup
        sources=[ev.buy_source_id, ev.sell_source_id],
        buy_source=ev.buy_source_id,
        sell_source=ev.sell_source_id,
        # normalised-forward legs — a mid-to-mid price pair is not meaningful here,
        # so the executable basis is reported directly as the profit estimate.
        buy_price=0.0,
        sell_price=0.0,
        estimated_profit_pips=ev.basis_pips,
        estimated_profit_pct=ev.basis_pips * USDINR_PIP / 85.0,   # ~USD/INR level
        latency_risk_ms=float(ev.offshore_staleness_ms),
        confidence_score=_confidence_for(ev),
        session=_session_for(ev.ts),
        timestamp_ms=int(ev.ts * 1000),
        window_size_ms=int(getattr(BASIS_DETECTION_CONFIG, "basis_window_ms", 2000)),
        details={
            "kind": "basis_event",
            "schema_version": ev.schema_version,
            "leg_pair": ev.leg_pair,
            "buy_leg": ev.buy_leg,
            "sell_leg": ev.sell_leg,
            "comparison_basis": ev.comparison_basis,
            "target_expiry": ev.target_expiry,
            "carry_adjustment_pips": ev.carry_adjustment_pips,
            "offshore_staleness_ms": ev.offshore_staleness_ms,
            "persistence_class": ev.persistence_class,
            "execution_verdict": ev.execution_verdict,
            "expected_slippage_pips": ev.expected_slippage_pips,
            "assumed_depth": ev.assumed_depth,
            "composite_score": ev.composite_score,
            "cadence": ev.cadence,
        },
    )
    return {
        "event_id": ev.event_id,
        "timestamp": ev.ts,
        "opportunity": opp.to_dict(),
        "raw_score": ev.basis_pips,
    }


async def consume_basis_stream(
    engine: Any,
    *,
    group: str = DEFAULT_GROUP,
    consumer: str = "semantic-1",
    batch: int = 32,
    block_ms: int = 2000,
    client: Any = None,
    stop_when_idle: bool = False,
) -> None:
    """
    Blocking loop: read BasisEvents from arbex.raw_opps (consumer group) and feed
    them to ``engine.process_raw_opp`` as adapted raw-opp messages. Replaces the
    Pub/Sub subscribe call when running in basis mode.

    ``client`` overrides the shared redis client (tests pass fakeredis).
    ``stop_when_idle`` returns once a read blocks out with no entries — a
    drain-and-exit mode used by the integration test.
    """
    if client is None:
        await redis_client.connect()
        client = redis_client._client  # redis.asyncio.Redis
    reader = BasisStreamConsumer(client, stream=RAW_STREAM, group=group, consumer=consumer)
    await reader.ensure_group()
    logger.info("SemanticEngine consuming basis stream %s (group %s)", RAW_STREAM, group)

    while getattr(engine, "_is_running", True):
        try:
            entries = await reader.read(count=batch, block_ms=block_ms)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # transient redis error — back off and retry
            logger.error("basis stream read failed: %s", e)
            await asyncio.sleep(1.0)
            continue
        if not entries and stop_when_idle:
            return
        for msg_id, ev in entries:
            try:
                await engine.process_raw_opp(basis_event_to_raw_msg(ev))
            except Exception as e:
                logger.error("SemanticEngine failed on basis event %s: %s", ev.event_id, e)
            finally:
                await reader.ack(msg_id)   # at-least-once: ack after processing
