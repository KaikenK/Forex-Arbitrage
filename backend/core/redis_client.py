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
            cls._instance._disabled = os.environ.get("ARBEX_REDIS", "").lower() in ("off", "0", "false")
            cls._instance._fail_streak = 0
        return cls._instance

    async def connect(self):
        # circuit breaker: after repeated refusals, stop trying (and stop logging).
        # Set ARBEX_REDIS=off to skip Redis entirely (dashboard works without it;
        # only the arbex.raw_opps stream + semantic engine need it).
        if self._disabled or self._fail_streak >= 3:
            raise ConnectionError("redis disabled / unreachable")
        async with self._connect_lock:
            if not self._is_connected:
                try:
                    self._client = redis.Redis(
                        host=self._host,
                        port=self._port,
                        decode_responses=True,
                        socket_connect_timeout=2,
                    )
                    await self._client.ping()
                    self._pubsub = self._client.pubsub()
                    self._is_connected = True
                    self._fail_streak = 0
                    logger.info(f"Connected to Redis at {self._host}:{self._port}")
                except Exception as e:
                    self._fail_streak += 1
                    if self._fail_streak <= 3:
                        logger.warning(
                            f"Redis unavailable ({e}) — continuing without it"
                            f"{' (further attempts suppressed)' if self._fail_streak == 3 else ''}")
                    raise

    async def close(self):
        if self._is_connected:
            if self._pubsub:
                await self._pubsub.close()
            if self._client:
                await self._client.aclose()
            self._is_connected = False

    async def publish(self, channel: str, message: Dict[str, Any]):
        """
        Publish a JSON dictionary message to a channel. Best-effort: if Redis is
        disabled or unreachable the circuit breaker in connect() has already
        logged (at most 3 times) — this call then silently no-ops rather than
        logging once per tick.
        """
        if not self._is_connected:
            try:
                await self.connect()
            except Exception:
                return
        try:
            payload = json.dumps(message)
            await self._client.publish(channel, payload)
        except Exception as e:
            if self._fail_streak < 3:
                logger.error(f"Error publishing to {channel}: {e}")

    async def xadd(
        self,
        stream: str,
        fields: Dict[str, str],
        maxlen: int = 50_000,
    ) -> Optional[str]:
        """
        Append an entry to a Redis Stream (capped, approximate trim). Used for
        arbex.raw_opps so a slow consumer cannot silently drop events. Best-effort
        — returns None and logs on failure rather than raising.
        """
        if not self._is_connected:
            try:
                await self.connect()
            except Exception:
                return None
        try:
            return await self._client.xadd(
                stream, fields, maxlen=maxlen, approximate=True
            )
        except Exception as e:
            if self._fail_streak < 3:
                logger.error(f"Error xadd to {stream}: {e}")
            return None

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
