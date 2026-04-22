import asyncio
import logging
from backend.core.streaming.adapters.base import BaseStreamAdapter
from backend.core.streaming.models import TickUpdate, OrderbookUpdate, OrderbookLevel
from backend.core.data_sources.session_synthetic_source import create_synthetic_sources

logger = logging.getLogger(__name__)

class SyntheticStreamAdapter(BaseStreamAdapter):
    """
    Wraps the existing SessionAwareSyntheticSource logic to fit the new
    streaming adapter pattern. Simulates both Tick data and L2 Orderbook data.
    """
    def __init__(self):
        super().__init__("Synthetic")
        self._sources = []

    async def connect(self):
        logger.info("Initializing Synthetic Stream Adapter sources...")
        self._sources = create_synthetic_sources()
        for src in self._sources:
            src.connect()
        self._is_running = True

    async def disconnect(self):
        self._is_running = False
        for src in self._sources:
            src.disconnect()
        logger.info("Synthetic Stream Adapter disconnected.")

    async def start_streaming(self):
        """
        Polls the synthetic generators and emits updates.
        """
        logger.info("Started Synthetic Stream Adapter loop.")
        while self._is_running:
            for src in self._sources:
                raw_tick = src.get_tick("USDINR")
                if raw_tick:
                    # 1. Emit Tick
                    tick = TickUpdate(
                        symbol=raw_tick.symbol,
                        source=raw_tick.source_id,
                        timestamp=raw_tick.timestamp_ms,
                        bid=raw_tick.bid,
                        ask=raw_tick.ask
                    )
                    await self._emit_tick(tick)
                    
                    # 2. Simulate Orderbook depth
                    # We create 5 levels of depth using the bid/ask as top of book
                    bids = []
                    asks = []
                    
                    # Generate a pseudo-random multiplier based on source_id length + timestamp
                    # so that different sources look visibly different in the UI
                    vol_multiplier = 1.0 + ((len(raw_tick.source_id) + int(raw_tick.timestamp_ms)) % 5) * 0.2
                    
                    for i in range(5):
                        price_offset = i * 0.005 # 0.5 pip steps
                        vol = int((10 - i) * 1000 * vol_multiplier) # Decreasing volume at depth
                        bids.append(OrderbookLevel(price=round(raw_tick.bid - price_offset, 4), volume=vol))
                        asks.append(OrderbookLevel(price=round(raw_tick.ask + price_offset, 4), volume=vol))
                        
                    ob = OrderbookUpdate(
                        symbol=raw_tick.symbol,
                        source=raw_tick.source_id,
                        timestamp=raw_tick.timestamp_ms,
                        bids=bids,
                        asks=asks
                    )
                    await self._emit_orderbook(ob)
                    
            await asyncio.sleep(0.01) # Poll at 100Hz
