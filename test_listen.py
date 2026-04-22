import asyncio
import redis.asyncio as redis
import json

async def listen_scored_opps():
    r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe('arbex.scored_opps')
    print("Listening to arbex.scored_opps...")
    
    try:
        async for message in pubsub.listen():
            if message['type'] == 'message':
                data = json.loads(message['data'])
                print(f"Received scored opp! Score: {data.get('composite_score')}")
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.close()
        await r.aclose()

if __name__ == "__main__":
    asyncio.run(listen_scored_opps())
