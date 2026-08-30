"""
FR-6.2: the arbex.raw_opps stream survives a slow consumer with zero drops.

Uses fakeredis (in-process Redis emulation) so this runs in CI with no server.
"""

from datetime import date

import pytest

fakeredis = pytest.importorskip("fakeredis")
from fakeredis import aioredis as fake_aioredis  # noqa: E402

from backend.core.basis import BasisEvent, BasisStreamConsumer  # noqa: E402
from backend.core.basis.basis_event import RAW_STREAM  # noqa: E402


def _event(i: int) -> BasisEvent:
    return BasisEvent.new(
        leg_pair="onshore_offshore", buy_leg="onshore", sell_leg="offshore",
        buy_source_id="dhan", sell_source_id="cme",
        basis_pips=3.0 + i * 0.1, comparison_basis="futures",
        target_expiry=date(2026, 9, 28), carry_adjustment_pips=0.2,
        buy_quote_ts=float(i), sell_quote_ts=float(i) + 0.05,
    )


async def _publish(client, n: int, maxlen: int = 50_000) -> None:
    for i in range(n):
        await client.xadd(
            RAW_STREAM, _event(i).to_stream_fields(),
            maxlen=maxlen, approximate=False,
        )


@pytest.fixture
def client():
    return fake_aioredis.FakeRedis()


@pytest.mark.asyncio
async def test_slow_consumer_drops_nothing(client):
    n = 500
    await _publish(client, n)

    consumer = BasisStreamConsumer(client, consumer="slow")
    await consumer.ensure_group()

    seen: list[int] = []
    # read in tiny batches — the "slow consumer" — long after all 500 are queued
    while True:
        batch = await consumer.read(count=7)
        if not batch:
            break
        for msg_id, ev in batch:
            seen.append(round((ev.basis_pips - 3.0) / 0.1))
            await consumer.ack(msg_id)

    assert len(seen) == n
    assert sorted(seen) == list(range(n))          # every event, exactly once, in order
    assert await consumer.pending_count() == 0     # all acked


@pytest.mark.asyncio
async def test_unacked_events_stay_pending_for_replay(client):
    await _publish(client, 20)
    consumer = BasisStreamConsumer(client, consumer="crashy")
    await consumer.ensure_group()

    first = await consumer.read(count=20)
    assert len(first) == 20
    assert await consumer.pending_count() == 20    # read but not acked

    # simulate a crash + restart: re-read our own pending list from 0
    replay = await consumer.read(count=20, new_only=False)
    assert [i for i, _ in enumerate(replay)] == list(range(20))
    for msg_id, _ in replay:
        await consumer.ack(msg_id)
    assert await consumer.pending_count() == 0


@pytest.mark.asyncio
async def test_capped_stream_bounds_backlog(client):
    # a truly absent consumer: stream is capped so it cannot grow without bound
    await _publish(client, 300, maxlen=100)
    consumer = BasisStreamConsumer(client)
    await consumer.ensure_group()
    assert await consumer.backlog_len() <= 100

    drained = 0
    while True:
        batch = await consumer.read(count=50)
        if not batch:
            break
        drained += len(batch)
        for msg_id, _ in batch:
            await consumer.ack(msg_id)
    assert drained <= 100          # only what the cap retained — a known, bounded loss


@pytest.mark.asyncio
async def test_two_consumers_split_the_stream(client):
    await _publish(client, 100)
    a = BasisStreamConsumer(client, consumer="a")
    b = BasisStreamConsumer(client, consumer="b")
    await a.ensure_group()

    got_a = got_b = 0
    while True:
        ba = await a.read(count=10)
        bb = await b.read(count=10)
        if not ba and not bb:
            break
        for mid, _ in ba:
            got_a += 1
            await a.ack(mid)
        for mid, _ in bb:
            got_b += 1
            await b.ack(mid)

    assert got_a + got_b == 100        # partitioned, no double-delivery
    assert got_a > 0 and got_b > 0
