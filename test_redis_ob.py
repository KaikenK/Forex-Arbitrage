import asyncio
import redis.asyncio as redis

async def listen():
    r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe('arbex.orderbooks')
    print("Listening to arbex.orderbooks...")
    try:
        async for message in pubsub.listen():
            print(f"Message: {message}")
            if message['type'] == 'message':
                print("Received orderbook update!")
                break
    finally:
        await pubsub.unsubscribe()
        await r.aclose()

if __name__ == "__main__":
    asyncio.run(listen())
