import asyncio
from backend.core.redis_client import redis_client

async def test_sub():
    async def cb1(data):
        print("cb1:", data)
    async def cb2(data):
        print("cb2:", data)

    # Spawn two subscribe calls concurrently, exactly like websocket_routes.py
    t1 = asyncio.create_task(redis_client.subscribe("channel1", cb1))
    t2 = asyncio.create_task(redis_client.subscribe("channel2", cb2))
    
    await asyncio.sleep(1)
    
    # Now publish
    await redis_client.publish("channel1", {"msg": "hello channel 1"})
    await redis_client.publish("channel2", {"msg": "hello channel 2"})
    await redis_client.publish("channel1", {"msg": "hello again 1"})
    
    await asyncio.sleep(2)
    t1.cancel()
    t2.cancel()

asyncio.run(test_sub())
