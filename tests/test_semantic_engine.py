import sys
import types

import pytest


redis_module = types.ModuleType("redis")
redis_asyncio_module = types.ModuleType("redis.asyncio")


class _FakeRedis:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    async def ping(self) -> None:
        return None

    def pubsub(self):
        return _FakePubSub()

    async def publish(self, channel: str, payload: str) -> None:
        del channel, payload

    async def aclose(self) -> None:
        return None


class _FakePubSub:
    async def close(self) -> None:
        return None

    async def subscribe(self, channel: str) -> None:
        del channel

    async def unsubscribe(self, channel: str) -> None:
        del channel

    async def listen(self):
        if False:
            yield None


redis_asyncio_module.Redis = _FakeRedis
redis_module.asyncio = redis_asyncio_module
sys.modules.setdefault("redis", redis_module)
sys.modules.setdefault("redis.asyncio", redis_asyncio_module)

from backend.core.arbitrage.arbitrage_engine import ArbitrageOpportunity
from backend.core.arbitrage.arbitrage_engine import ArbitrageType
from backend.core.semantic_engine import NewsBias
from backend.core.semantic_engine import SemanticEngine


def make_opportunity(confidence: float = 0.82) -> ArbitrageOpportunity:
    return ArbitrageOpportunity(
        type=ArbitrageType.CROSS_SOURCE,
        symbols=["USDINR"],
        sources=["source_a", "source_b"],
        buy_source="source_a",
        sell_source="source_b",
        buy_price=83.1500,
        sell_price=83.1750,
        estimated_profit_pips=2.5,
        estimated_profit_pct=0.0003,
        latency_risk_ms=45.0,
        confidence_score=confidence,
        session="LONDON",
        timestamp_ms=1710000000000,
        window_size_ms=20,
    )


class FailingNewsBiasProvider:
    async def get_bias(self, symbol: str) -> NewsBias:
        raise RuntimeError("news service unavailable")


class FixedNewsBiasProvider:
    def __init__(self, bias: NewsBias) -> None:
        self._bias = bias

    async def get_bias(self, symbol: str) -> NewsBias:
        return self._bias


@pytest.mark.asyncio
async def test_process_raw_opp_publishes_neutral_fallback_when_news_service_fails() -> None:
    published = []

    async def fake_publisher(channel: str, message: dict) -> None:
        published.append((channel, message))

    engine = SemanticEngine(
        news_bias_provider=FailingNewsBiasProvider(),
        publisher=fake_publisher,
    )

    raw_opp = make_opportunity()
    await engine.process_raw_opp({"event_id": "evt-1", "opportunity": raw_opp.to_dict()})

    assert len(published) == 1
    channel, message = published[0]
    assert channel == "arbex.scored_opps"
    assert message["news_bias"]["status"] == "neutral_fallback"
    assert message["opportunity"]["confidence_score"] == pytest.approx(raw_opp.confidence_score)
    assert any("neutral fallback" in reason.lower() for reason in message["execution"]["verdict_reasons"])


@pytest.mark.asyncio
async def test_process_raw_opp_applies_news_risk_penalty_to_scored_confidence() -> None:
    published = []

    async def fake_publisher(channel: str, message: dict) -> None:
        published.append((channel, message))

    bias = NewsBias(
        pair="USD/INR",
        status="live",
        window_minutes=30,
        overall_sentiment=-0.7,
        semantic_score=-0.42,
        confidence=0.8,
        confidence_adjustment=-0.04,
        direction="risk_off",
        regime="high_conviction",
        item_count=4,
        bullish_count=0,
        bearish_count=4,
        neutral_count=0,
        freshness_seconds=0.0,
        service_updated_at="2026-04-23T12:00:00+00:00",
        top_drivers=[{"headline": "RBI surprise"}],
        reason="NEWS RISK: USD/INR sentiment -0.70, semantic score -0.42, 4 articles over 30m, confidence -0.04.",
    )
    engine = SemanticEngine(
        news_bias_provider=FixedNewsBiasProvider(bias),
        publisher=fake_publisher,
    )

    raw_opp = make_opportunity(confidence=0.82)
    await engine.process_raw_opp({"event_id": "evt-2", "opportunity": raw_opp.to_dict()})

    assert len(published) == 1
    _, message = published[0]
    assert message["news_bias"]["pair"] == "USD/INR"
    assert message["news_bias"]["semantic_score"] == pytest.approx(-0.42)
    assert message["news_bias"]["direction"] == "risk_off"
    assert message["news_bias"]["regime"] == "high_conviction"
    assert message["opportunity"]["confidence_score"] == pytest.approx(0.78)
    assert message["execution"]["feasibility_score"] < 100.0
    assert "News risk -0.04 (live)" in message["ranking_reason"]