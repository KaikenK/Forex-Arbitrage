from abc import ABC, abstractmethod
from typing import Callable, Awaitable, Any
import asyncio
from backend.core.streaming.models import OrderbookUpdate, TickUpdate

class BaseStreamAdapter(ABC):
    """
    Abstract base class for all Market Data Stream Adapters.
    Adapters are responsible for connecting to an external source,
    fetching/streaming pricing or orderbook data, and invoking the 
    registered callback functions when new data arrives.
    """
    
    def __init__(self, source_name: str):
        self.source_name = source_name
        self._tick_callbacks = []
        self._orderbook_callbacks = []
        self._is_running = False

    def on_tick(self, callback: Callable[[TickUpdate], Awaitable[None]]):
        """Register a callback for Top-of-Book (BBO) updates."""
        self._tick_callbacks.append(callback)

    def on_orderbook(self, callback: Callable[[OrderbookUpdate], Awaitable[None]]):
        """Register a callback for Level-2 Orderbook updates."""
        self._orderbook_callbacks.append(callback)

    async def _emit_tick(self, update: TickUpdate):
        """Internal method to broadcast tick to all listeners."""
        tasks = [callback(update) for callback in self._tick_callbacks]
        if tasks:
            await asyncio.gather(*tasks)

    async def _emit_orderbook(self, update: OrderbookUpdate):
        """Internal method to broadcast orderbook to all listeners."""
        tasks = [callback(update) for callback in self._orderbook_callbacks]
        if tasks:
            await asyncio.gather(*tasks)

    @abstractmethod
    async def connect(self):
        """Initialize connection to the data source."""
        pass

    @abstractmethod
    async def disconnect(self):
        """Gracefully shutdown the connection."""
        pass

    @abstractmethod
    async def start_streaming(self):
        """Begin the continuous streaming loop."""
        pass
