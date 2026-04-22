import asyncio
import redis.asyncio as redis
import json

async def listen_raw_opps():
    r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe('arbex.raw_opps')
    print("Listening to arbex.raw_opps...", flush=True)
    
    try:
        async for message in pubsub.listen():
            if message['type'] == 'message':
                print("Received raw opp!", flush=True)
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.close()
        await r.aclose()

if __name__ == "__main__":
    asyncio.run(listen_raw_opps())
