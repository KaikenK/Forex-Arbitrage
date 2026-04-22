import os
import json
import logging
import asyncio
from typing import Any, Dict, Optional, Callable
import redis.asyncio as redis

logger = logging.getLogger(__name__)

class RedisClient:
    """
    Singleton-pattern Redis client for async pub/sub operations.
    Handles connection management and standardized publishing/subscribing.
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(RedisClient, cls).__new__(cls)
            cls._instance._client = None
            cls._instance._pubsub = None
            cls._instance._is_connected = False
            cls._instance._host = os.environ.get("REDIS_HOST", "localhost")
            cls._instance._port = int(os.environ.get("REDIS_PORT", 6379))
            cls._instance._connect_lock = asyncio.Lock()
        return cls._instance

    async def connect(self):
        async with self._connect_lock:
            if not self._is_connected:
                try:
                    self._client = redis.Redis(
                        host=self._host, 
                        port=self._port, 
                        decode_responses=True
                    )
                    await self._client.ping()
                    self._pubsub = self._client.pubsub()
                    self._is_connected = True
                    logger.info(f"Connected to Redis at {self._host}:{self._port}")
                except Exception as e:
                    logger.error(f"Failed to connect to Redis: {e}")
                    raise

    async def close(self):
        if self._is_connected:
            if self._pubsub:
                await self._pubsub.close()
            if self._client:
                await self._client.aclose()
            self._is_connected = False

    async def publish(self, channel: str, message: Dict[str, Any]):
        """Publish a JSON dictionary message to a channel."""
        if not self._is_connected:
            await self.connect()
        try:
            payload = json.dumps(message)
            await self._client.publish(channel, payload)
        except Exception as e:
            logger.error(f"Error publishing to {channel}: {e}")

    async def subscribe(self, channel: str, callback: Callable[[Dict[str, Any]], None]):
        """Subscribe to a channel and handle messages with the provided callback."""
        await self.subscribe_many({channel: callback})

    async def subscribe_many(self, channel_callbacks: Dict[str, Callable[[Dict[str, Any]], None]]):
        """Subscribe to multiple channels and route messages to their respective callbacks."""
        if not self._is_connected:
            await self.connect()
        
        for channel in channel_callbacks.keys():
            await self._pubsub.subscribe(channel)
            logger.info(f"Subscribed to Redis channel: {channel}")
            
        try:
            async for message in self._pubsub.listen():
                if message["type"] == "message":
                    channel = message["channel"]
                    if channel in channel_callbacks:
                        try:
                            data = json.loads(message["data"])
                            cb = channel_callbacks[channel]
                            if asyncio.iscoroutinefunction(cb):
                                await cb(data)
                            else:
                                cb(data)
                        except json.JSONDecodeError:
                            logger.error(f"Failed to decode message from {channel}")
                        except Exception as e:
                            logger.error(f"Error in subscriber callback for {channel}: {e}")
        except asyncio.CancelledError:
            for channel in channel_callbacks.keys():
                logger.info(f"Unsubscribing from {channel}")
                await self._pubsub.unsubscribe(channel)
            raise

redis_client = RedisClient()
