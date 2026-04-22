import asyncio
import redis.asyncio as redis
import time
import json
import random

SESSIONS = ["tokyo", "london", "newyork"]
PROVIDERS = ["bloomberg", "reuters", "mt5"]

async def inject_fake_opps_loop():
    r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    print("Starting cascading injection to arbex.scored_opps...")
    
    count = 0
    total_ticks = 1000
    while True:
        # Generate random sessions and providers
        buy_session = random.choice(SESSIONS)
        sell_session = random.choice(SESSIONS)
        buy_provider = random.choice(PROVIDERS)
        sell_provider = random.choice(PROVIDERS)
        
        # Ensure they are different
        if buy_session == sell_session and buy_provider == sell_provider:
            sell_provider = [p for p in PROVIDERS if p != buy_provider][0]

        buy_source = f"{buy_provider}_{buy_session}_usdinr"
        sell_source = f"{sell_provider}_{sell_session}_usdinr"
        
        # Randomize prices, confidence, and score
        buy_price = 93.80 + random.uniform(-0.1, 0.1)
        sell_price = buy_price + random.uniform(0.0003, 0.0015)
        profit_pips = (sell_price - buy_price) * 10000
        
        conf = random.uniform(0.65, 0.99)
        score = conf * 100 + random.uniform(-5.0, 5.0)
        
        msg = {
            "event_id": f"cascade-event-{count}",
            "timestamp": time.time(),
            "opportunity": {
                "type": "cross_provider" if buy_session == sell_session else "cross_session",
                "symbols": ["USDINR"],
                "buy_source": buy_source,
                "sell_source": sell_source,
                "buy_price": buy_price,
                "sell_price": sell_price,
                "estimated_profit_pips": profit_pips,
                "confidence_score": conf,
                "latency_risk_ms": random.randint(5, 50)
            },
            "composite_score": min(100.0, max(0.0, score)),
            "dimension_scores": {},
            "ranking_reason": "Cascade Test",
            "rank": random.randint(1, 3),
            "persistence": {
                "persistence_class": random.choice(["persistent", "flickering", "ephemeral"]), 
                "duration_ms": random.randint(100, 3000)
            },
            "execution": {
                "verdict": random.choice(["viable", "risky", "unknown"])
            }
        }
        await r.publish('arbex.scored_opps', json.dumps(msg))
        print(f"Published cascading opportunity {count}", flush=True)
        count += 1
        
        # Also emit a fake tick to increment totalTicksLogged in useWebSocket.ts
        # which is listening on sources WS via broadcast_source_comparison
        # We can just publish directly to arbex.raw_opps or simulate sources payload via redis? No, the frontend listens to ws://127.0.0.1:8000/ws/sources/USDINR
        # The python server listens to pub/sub... Wait, we can't easily fake the python internal ws_manager without pushing through the proper channels.
        # But for now, fixing the cascading opportunities UI is the most important part!
        
        # Sleep randomly between 0.5s to 2s to create a cascading effect
        await asyncio.sleep(random.uniform(0.5, 2.0))

if __name__ == "__main__":
    asyncio.run(inject_fake_opps_loop())
