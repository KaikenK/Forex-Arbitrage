"""In-process scorer: the dashboard stream without Redis, display only (bug review 2026-11-05)."""
import asyncio
import json
import sys
import types

import pytest

redis_module = types.ModuleType("redis")
redis_asyncio_module = types.ModuleType("redis.asyncio")


class _FakeRedis:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs


redis_asyncio_module.Redis = _FakeRedis
redis_module.asyncio = redis_asyncio_module
sys.modules.setdefault("redis", redis_module)
sys.modules.setdefault("redis.asyncio", redis_asyncio_module)

from backend.core.arbitrage.arbitrage_engine import ArbitrageOpportunity, ArbitrageType  # noqa: E402
from backend.core.semantic_engine import SemanticEngine  # noqa: E402
from backend.server.inprocess_scorer import DISPLAY_TAG, InProcessScorer, dashboard_stats  # noqa: E402


def make_opportunity() -> ArbitrageOpportunity:
    return ArbitrageOpportunity(
        type=ArbitrageType.CROSS_SOURCE, symbols=["EURUSD"], sources=["spot_kraken_eurusd", "spot_truefx_eurusd"],
        buy_source="spot_kraken_eurusd", sell_source="spot_truefx_eurusd", buy_price=1.12568, sell_price=1.12612,
        estimated_profit_pips=4.4, estimated_profit_pct=0.0004, latency_risk_ms=1500.0, confidence_score=0.55,
        session="LONDON", timestamp_ms=1793858000000, window_size_ms=500)


class _NoNews:
    async def get_bias(self, symbol):
        raise RuntimeError("news service unavailable")


def _factory(publish):
    return SemanticEngine(news_bias_provider=_NoNews(), publisher=publish, currency_bias=False)


@pytest.mark.asyncio
async def test_scores_once_per_route_and_tags_display():
    sent = []

    async def broadcast(msg):
        sent.append(msg)

    scorer = InProcessScorer(_factory, broadcast, mode="on")
    opp = make_opportunity()
    first = scorer(opp)
    assert scorer(opp) is None and scorer.skipped_busy == 1      # same route still in flight
    await first
    assert len(sent) == 1 and scorer.scored == 1
    assert sent[0]["opportunity"]["buy_source"] == "spot_kraken_eurusd"
    assert sent[0]["ranking_reason"].startswith(DISPLAY_TAG)
    await scorer(opp)                                             # free again after it finished
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_auto_mode_defers_to_redis_and_off_builds_nothing():
    async def broadcast(msg):
        raise AssertionError("must not broadcast")

    redis_up = InProcessScorer(_factory, broadcast, mode="auto", redis_connected=lambda: True)
    assert redis_up(make_opportunity()) is None
    off = InProcessScorer(_factory, broadcast, mode="off")
    assert off._engine is None and off(make_opportunity()) is None


@pytest.mark.asyncio
async def test_recorder_output_is_identical_with_scorer_on_or_off():
    def run(mode):
        rows = []

        def recorder(opp):                       # stands in for main._log_live_opportunity
            rows.append(json.dumps(opp.to_dict(), sort_keys=True))

        async def broadcast(msg):
            pass

        scorer = InProcessScorer(_factory, broadcast, mode=mode)
        opp = make_opportunity()
        recorder(opp)
        task = scorer(opp)
        return rows, task, opp

    rows_off, _, _ = run("off")
    rows_on, task, opp = run("on")
    await task
    assert rows_on == rows_off
    assert json.dumps(opp.to_dict(), sort_keys=True) == rows_on[0]     # scoring did not mutate the opportunity


def test_dashboard_stats_shape_matches_the_dashboard_parser():
    payload = dashboard_stats({"ticks_processed": 2000, "arbitrage_opportunities_detected": 5})
    assert payload == {"data": {"ticks_processed": 2000, "opportunities_detected": 5, "detection_rate_pct": 0.25}}
    assert dashboard_stats({})["data"]["detection_rate_pct"] == 0.0


@pytest.mark.asyncio
async def test_stats_reach_the_dashboard_socket_as_type_stats():
    from backend.server.websocket_routes import WebSocketManager

    class _Socket:
        def __init__(self):
            self.sent = []

        async def send_text(self, text):
            self.sent.append(text)

    manager = WebSocketManager()
    sock = _Socket()
    manager.connections.setdefault("dashboard:unified", set()).add(sock)
    await manager.broadcast_to_dashboard("stats", dashboard_stats({"ticks_processed": 10}))
    message = json.loads(sock.sent[0])
    assert message["type"] == "stats" and message["data"]["ticks_processed"] == 10
