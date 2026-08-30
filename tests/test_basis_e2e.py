"""
End-to-end Phase-3 chain, no Redis server and no Dhan:

    BasisEvent -> xadd(arbex.raw_opps stream)
              -> consume_basis_stream (consumer group)
              -> basis_event_to_raw_msg adapter
              -> SemanticEngine.process_raw_opp (persistence + execution + rank)
              -> publish(arbex.scored_opps)

Proves the frozen schema, the stream transport and the semantic adapter line up.
"""

from datetime import date

import pytest

fakeredis = pytest.importorskip("fakeredis")
from fakeredis import aioredis as fake_aioredis  # noqa: E402

from backend.core.basis import BasisEvent  # noqa: E402
from backend.core.basis.basis_event import RAW_STREAM, SCORED_STREAM  # noqa: E402
from backend.core.basis.semantic_adapter import consume_basis_stream  # noqa: E402
from backend.core.semantic_engine import NewsBias, SemanticEngine  # noqa: E402


class _StubNews:
    async def get_bias(self, symbol: str) -> NewsBias:
        return NewsBias(pair="USD/INR", status="live", window_minutes=30,
                        overall_sentiment=0.0, confidence_adjustment=0.0,
                        reason="stub: neutral")


def _event(i: int, **kw) -> BasisEvent:
    base = dict(
        leg_pair="onshore_offshore", buy_leg="onshore", sell_leg="offshore",
        buy_source_id="dhan", sell_source_id="cme",
        basis_pips=5.0 + i, comparison_basis="futures",
        target_expiry=date(2026, 9, 28), carry_adjustment_pips=0.3,
        buy_quote_ts=float(i), sell_quote_ts=float(i) + 0.1,
        persistence_class="flickering", execution_verdict="viable",
    )
    base.update(kw)
    return BasisEvent.new(**base)


@pytest.mark.asyncio
async def test_basis_events_flow_through_to_scored_stream():
    client = fake_aioredis.FakeRedis()

    for i in range(3):
        await client.xadd(RAW_STREAM, _event(i).to_stream_fields())

    published = []

    async def capture(channel, message):
        published.append((channel, message))

    engine = SemanticEngine(news_bias_provider=_StubNews(), publisher=capture)
    engine._is_running = True

    await consume_basis_stream(engine, client=client, block_ms=50, stop_when_idle=True)

    assert len(published) == 3
    channels = {c for c, _ in published}
    assert channels == {SCORED_STREAM}

    msg = published[0][1]
    opp = msg["opportunity"]
    assert opp["symbols"] == ["USDINR"]
    assert opp["details"]["kind"] == "basis_event"
    assert opp["details"]["leg_pair"] == "onshore_offshore"
    assert opp["details"]["target_expiry"] == "2026-09-28"
    assert "composite_score" in msg
    assert msg["event_id"]

    # every stream entry was acked -> nothing left pending
    pend = await client.xpending(RAW_STREAM, "arbex.semantic")
    pending_n = pend["pending"] if isinstance(pend, dict) else pend[0]
    assert pending_n == 0


@pytest.mark.asyncio
async def test_adapter_maps_basis_number_to_profit_and_confidence():
    client = fake_aioredis.FakeRedis()
    await client.xadd(RAW_STREAM, _event(0, basis_pips=9.4,
                                         execution_verdict="unlikely",
                                         persistence_class="ephemeral").to_stream_fields())
    got = []

    async def capture(channel, message):
        got.append(message)

    engine = SemanticEngine(news_bias_provider=_StubNews(), publisher=capture)
    engine._is_running = True
    await consume_basis_stream(engine, client=client, block_ms=50, stop_when_idle=True)

    assert got[0]["opportunity"]["estimated_profit_pips"] == pytest.approx(9.4)
    # unlikely + ephemeral -> low confidence
    assert got[0]["opportunity"]["confidence_score"] < 0.5
