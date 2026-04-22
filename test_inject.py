import asyncio
import redis.asyncio as redis
import time
import json

async def inject_fake_opp():
    r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    msg = {
        "event_id": "test-event-123",
        "timestamp": time.time(),
        "opportunity": {
            "type": "cross_provider",
            "symbols": ["USDINR"],
            "buy_source": "bloomberg_tokyo_usdinr",
            "sell_source": "reuters_tokyo_usdinr",
            "buy_price": 93.80,
            "sell_price": 93.90,
            "estimated_profit_pips": 10.0,
            "confidence_score": 0.99,
            "latency_risk_ms": 10
        },
        "composite_score": 95.0,
        "dimension_scores": {},
        "ranking_reason": "Test",
        "rank": 1,
        "persistence": {"persistence_class": "persistent", "duration_ms": 1000},
        "execution": {"verdict": "viable"}
    }
    await r.publish('arbex.scored_opps', json.dumps(msg))
    print("Published fake opportunity to arbex.scored_opps")
    await r.aclose()

if __name__ == "__main__":
    asyncio.run(inject_fake_opp())
