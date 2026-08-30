"""
Consumer-group reader for the ``arbex.raw_opps`` basis-event stream.

The detection side appends events with ``RedisClient.xadd`` (capped, so a slow or
absent consumer can never grow the stream without bound). The semantic engine —
or any downstream consumer — reads them back through a *consumer group* so that:

* every event is delivered at least once (unacked entries stay in the group's
  pending list and can be re-read / claimed after a crash), and
* a deliberately slow consumer falls behind but **drops nothing** — it just has a
  longer pending list, bounded only by the stream's ``maxlen``.

This module is the reference consumer used by the FR-6.2 replay test
(``tests/test_stream_consumer.py``); the real semantic engine is free to use its
own client as long as it speaks the same group semantics.

SPEC: docs/SPEC.md section 6 (FR-6.2) and section 7 (schema).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from backend.core.basis.basis_event import BasisEvent, RAW_STREAM

logger = logging.getLogger(__name__)

DEFAULT_GROUP = "arbex.semantic"


@dataclass
class BasisStreamConsumer:
    """
    Thin wrapper over ``redis.asyncio`` stream consumer-group calls.

    ``client`` is any object exposing the async ``xgroup_create`` / ``xreadgroup``
    / ``xack`` / ``xpending`` methods (``redis.asyncio.Redis`` or
    ``fakeredis.aioredis.FakeRedis``). Decoupled from the ``RedisClient``
    singleton on purpose so it is trivially testable.
    """

    client: Any
    stream: str = RAW_STREAM
    group: str = DEFAULT_GROUP
    consumer: str = "c1"

    async def ensure_group(self) -> None:
        """Create the group (and the stream, via MKSTREAM) if it does not exist."""
        try:
            await self.client.xgroup_create(
                self.stream, self.group, id="0", mkstream=True
            )
        except Exception as e:  # redis.exceptions.ResponseError: BUSYGROUP
            if "BUSYGROUP" not in str(e):
                raise

    async def read(
        self,
        count: int = 10,
        block_ms: Optional[int] = None,
        *,
        new_only: bool = True,
    ) -> List[Tuple[str, BasisEvent]]:
        """
        Read up to ``count`` undelivered entries. ``new_only`` reads the group's
        never-delivered backlog (``>``); set it False to re-read this consumer's
        own pending (unacked) entries from the start.
        """
        last_id = ">" if new_only else "0"
        resp = await self.client.xreadgroup(
            self.group, self.consumer,
            {self.stream: last_id},
            count=count,
            block=block_ms,
        )
        out: List[Tuple[str, BasisEvent]] = []
        for _stream, entries in resp or []:
            for msg_id, fields in entries:
                out.append((_decode(msg_id), _to_event(fields)))
        return out

    async def ack(self, *msg_ids: str) -> int:
        if not msg_ids:
            return 0
        return await self.client.xack(self.stream, self.group, *msg_ids)

    async def pending_count(self) -> int:
        summary = await self.client.xpending(self.stream, self.group)
        # redis-py returns a dict; fakeredis returns the same shape
        if isinstance(summary, dict):
            return int(summary.get("pending", 0))
        return int(summary[0]) if summary else 0

    async def backlog_len(self) -> int:
        return int(await self.client.xlen(self.stream))


def _decode(v: Any) -> str:
    return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)


def _to_event(fields: Any) -> BasisEvent:
    decoded = {_decode(k): _decode(val) for k, val in fields.items()}
    return BasisEvent.from_stream_fields(decoded)
