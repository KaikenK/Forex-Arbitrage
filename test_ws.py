import asyncio
import websockets

async def test_ws():
    uri = "ws://127.0.0.1:8000/ws/arbitrage"
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected to ws://127.0.0.1:8000/ws/arbitrage")
            # Wait for 5 seconds to receive messages
            try:
                for i in range(5):
                    message = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                    print(f"Received: {message}")
            except asyncio.TimeoutError:
                print("Timeout waiting for messages")
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_ws())
