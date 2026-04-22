import asyncio
import logging
import json
from typing import List
from backend.core.streaming.adapters.base import BaseStreamAdapter
from backend.core.streaming.models import OrderbookUpdate, TickUpdate

logger = logging.getLogger(__name__)

class OrderbookService:
    """
    Centralized service that manages data stream adapters.
    Normalizes incoming data and publishes it to Redis.
    """
    def __init__(self, redis_client=None):
        self._adapters: List[BaseStreamAdapter] = []
        self._is_running = False
        self._streaming_tasks = []
        self.redis = redis_client
        self._orderbook_channel = "arbex.orderbooks"
        self._tick_channel = "arbex.ticks"

    def register_adapter(self, adapter: BaseStreamAdapter):
        """Register a new stream adapter with the service."""
        # Hook up the callbacks
        adapter.on_tick(self._handle_tick)
        adapter.on_orderbook(self._handle_orderbook)
        self._adapters.append(adapter)
        logger.info(f"Registered stream adapter: {adapter.source_name}")

    async def _handle_tick(self, tick: TickUpdate):
        """Callback for when an adapter emits a tick."""
        if self.redis:
            try:
                payload = tick.model_dump()
                await self.redis.publish(self._tick_channel, payload)
            except Exception as e:
                logger.error(f"Failed to publish tick to Redis: {e}")

    async def _handle_orderbook(self, ob: OrderbookUpdate):
        """Callback for when an adapter emits an orderbook update."""
        if self.redis:
            try:
                payload = ob.model_dump()
                await self.redis.publish(self._orderbook_channel, payload)
            except Exception as e:
                logger.error(f"Failed to publish orderbook to Redis: {e}")

    async def start(self):
        """Connect all adapters and start streaming."""
        logger.info("Starting OrderbookService...")
        self._is_running = True
        
        for adapter in self._adapters:
            try:
                await adapter.connect()
                # Run the start_streaming method as a background task
                task = asyncio.create_task(adapter.start_streaming())
                self._streaming_tasks.append(task)
            except Exception as e:
                logger.error(f"Failed to start adapter {adapter.source_name}: {e}")

        logger.info(f"OrderbookService started with {len(self._adapters)} adapters.")

    async def stop(self):
        """Disconnect all adapters and stop streaming."""
        logger.info("Stopping OrderbookService...")
        self._is_running = False
        
        for adapter in self._adapters:
            try:
                await adapter.disconnect()
            except Exception as e:
                logger.error(f"Failed to stop adapter {adapter.source_name}: {e}")
                
        # Cancel all background tasks
        for task in self._streaming_tasks:
            task.cancel()
            
        self._streaming_tasks.clear()
        logger.info("OrderbookService stopped.")
