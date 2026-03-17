"""
Tick Streamer Module

Handles infinite tick streaming from MT5 with enriched metrics.
Publishes enriched ticks to WebSocket manager for distribution to clients.
"""

import asyncio
import logging
import time
import statistics
from typing import Callable, Dict, Any, Optional
from collections import deque
from datetime import datetime
from backend.core.mt5_client import MT5Client

logger = logging.getLogger(__name__)


def detect_session(timestamp_ms: int) -> str:
    """
    Detect trading session based on UTC time.
    
    Args:
        timestamp_ms: Unix timestamp in milliseconds
    
    Returns:
        Session name: "Tokyo", "London", "New York", or "Closed"
    """
    dt = datetime.utcfromtimestamp(timestamp_ms / 1000.0)
    hour = dt.hour
    
    # Tokyo: 00:00-09:00 UTC
    if 0 <= hour < 9:
        return "Tokyo"
    # London: 08:00-17:00 UTC
    elif 8 <= hour < 17:
        return "London"
    # New York: 13:00-22:00 UTC
    elif 13 <= hour < 22:
        return "New York"
    else:
        return "Closed"


class TickStreamer:
    """
    Streams enriched ticks from MT5 and publishes to WebSocket manager.
    Calculates velocity, acceleration, volatility, direction bias, and session.
    """
    
    def __init__(self, mt5_client: MT5Client, ws_manager, bar_aggregators=None):
        """
        Initialize tick streamer.
        
        Args:
            mt5_client: MT5Client instance
            ws_manager: WebSocket manager for broadcasting
            bar_aggregators: Dict of bar aggregators per symbol {symbol: MultiIntervalAggregator}
        """
        self.mt5_client = mt5_client
        self.ws_manager = ws_manager
        self.bar_aggregators = bar_aggregators or {}
        self.running = False
        self.streams = {}  # {symbol: task}
        
        # Tick buffers per symbol: {symbol: deque(maxlen=500)}
        self.last_ticks: Dict[str, deque] = {}
        
        # Velocity EMA per symbol for direction bias: {symbol: float}
        self.velocity_ema: Dict[str, float] = {}
        self.ema_alpha = 0.1  # EMA smoothing factor
        
        # Previous velocity per symbol: {symbol: float}
        self.prev_velocity: Dict[str, float] = {}
    
    async def start_stream(self, symbol: str):
        """
        Start streaming ticks for a symbol.
        
        Args:
            symbol: Symbol to stream
        """
        if symbol in self.streams:
            logger.warning(f"Tick stream already running for {symbol}")
            return
        
        # Initialize buffers for this symbol
        if symbol not in self.last_ticks:
            self.last_ticks[symbol] = deque(maxlen=500)
        if symbol not in self.velocity_ema:
            self.velocity_ema[symbol] = 0.0
        if symbol not in self.prev_velocity:
            self.prev_velocity[symbol] = 0.0
        
        logger.info(f"Starting tick stream for {symbol}")
        task = asyncio.create_task(self._stream_loop(symbol))
        self.streams[symbol] = task
    
    async def stop_stream(self, symbol: str):
        """Stop streaming ticks for a symbol."""
        if symbol in self.streams:
            task = self.streams.pop(symbol)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info(f"Stopped tick stream for {symbol}")
    
    def _calculate_volatility(self, symbol: str, window_seconds: int) -> float:
        """
        Calculate volatility (stddev) of mid prices over a time window.
        
        Args:
            symbol: Symbol name
            window_seconds: Time window in seconds
        
        Returns:
            Volatility (standard deviation) or 0.0 if insufficient data
        """
        if symbol not in self.last_ticks:
            return 0.0
        
        ticks = list(self.last_ticks[symbol])
        if len(ticks) < 2:
            return 0.0
        
        current_time_ms = ticks[-1]["time_ms"] if ticks else int(time.time() * 1000)
        window_start_ms = current_time_ms - (window_seconds * 1000)
        
        # Filter ticks within window
        window_ticks = [t for t in ticks if t["time_ms"] >= window_start_ms]
        
        if len(window_ticks) < 2:
            return 0.0
        
        mid_prices = [t["mid"] for t in window_ticks]
        return float(statistics.stdev(mid_prices)) if len(mid_prices) > 1 else 0.0
    
    def _enrich_tick(self, symbol: str, raw_tick: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enrich raw tick with calculated metrics.
        
        Args:
            symbol: Symbol name
            raw_tick: Raw tick dict with "time", "bid", "ask"
        
        Returns:
            Enriched tick dict with all metrics
        """
        time_ms = raw_tick["time"]
        bid = raw_tick["bid"]
        ask = raw_tick["ask"]
        mid = (bid + ask) / 2.0
        spread = ask - bid
        
        # Calculate velocity (price change)
        velocity = 0.0
        if symbol in self.last_ticks and len(self.last_ticks[symbol]) > 0:
            last_tick = self.last_ticks[symbol][-1]
            velocity = mid - last_tick["mid"]
        
        # Calculate acceleration (velocity change)
        acceleration = 0.0
        if symbol in self.prev_velocity:
            acceleration = velocity - self.prev_velocity[symbol]
        self.prev_velocity[symbol] = velocity
        
        # Update velocity EMA for direction bias
        if symbol in self.velocity_ema:
            self.velocity_ema[symbol] = (self.ema_alpha * velocity) + ((1 - self.ema_alpha) * self.velocity_ema[symbol])
        else:
            self.velocity_ema[symbol] = velocity
        
        # Direction bias: +1 for bullish, -1 for bearish
        direction_bias = 1 if self.velocity_ema[symbol] > 0 else -1
        
        # Calculate volatilities
        vol_5s = self._calculate_volatility(symbol, 5)
        vol_30s = self._calculate_volatility(symbol, 30)
        vol_1m = self._calculate_volatility(symbol, 60)
        
        # Detect session
        session = detect_session(time_ms)
        
        # Get tick count
        tick_count = len(self.last_ticks[symbol]) if symbol in self.last_ticks else 0
        
        # Get volume_real and broker_time from raw tick
        volume_real = raw_tick.get("volume_real", 0)
        broker_time = raw_tick.get("broker_time", datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
        
        # Create enriched tick
        enriched_tick = {
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread": spread,
            "volume_real": volume_real,
            "time": time_ms,
            "broker_time": broker_time
        }
        
        # Add to buffer
        self.last_ticks[symbol].append({
            "time_ms": time_ms,
            "bid": bid,
            "ask": ask,
            "mid": mid
        })
        
        return enriched_tick
    
    async def _stream_loop(self, symbol: str):
        """
        Main streaming loop for a symbol.
        
        Args:
            symbol: Symbol to stream
        """
        if not self.mt5_client.select_symbol(symbol):
            logger.error(f"Cannot stream ticks for {symbol}")
            return
        
        last_tick_time = 0
        tick_count = 0
        last_log_time = asyncio.get_event_loop().time()
        no_tick_count = 0
        
        logger.info(f"[{symbol}] Stream loop started, polling for ticks...")
        
        try:
            while True:
                # Ensure MT5 is connected
                if not self.mt5_client.ensure_connected():
                    logger.warning(f"[{symbol}] MT5 disconnected, waiting to reconnect...")
                    await asyncio.sleep(5)
                    continue
                
                # Get latest tick
                raw_tick = self.mt5_client.get_tick(symbol)
                
                if raw_tick and raw_tick["time"] != last_tick_time:
                    last_tick_time = raw_tick["time"]
                    tick_count += 1
                    no_tick_count = 0  # Reset no-tick counter
                    
                    # Enrich tick with metrics
                    enriched_tick = self._enrich_tick(symbol, raw_tick)
                    
                    # Log every tick (one line per tick as requested)
                    logger.info(
                        f"[{symbol}] Tick #{tick_count}: "
                        f"bid={enriched_tick['bid']:.5f} "
                        f"ask={enriched_tick['ask']:.5f} "
                        f"mid={enriched_tick['mid']:.5f} "
                        f"spread={enriched_tick['spread']:.5f} "
                        f"vol={enriched_tick['volume_real']} "
                        f"time={enriched_tick['broker_time']}"
                    )
                    
                    # Broadcast enriched tick to WebSocket clients
                    await self.ws_manager.broadcast_tick(symbol, enriched_tick)
                    
                    # Broadcast market state periodically (every 10 ticks)
                    if not hasattr(self, '_market_state_counters'):
                        self._market_state_counters = {}
                    if symbol not in self._market_state_counters:
                        self._market_state_counters[symbol] = 0
                    self._market_state_counters[symbol] += 1
                    
                    if self._market_state_counters[symbol] % 10 == 0:
                        await self.ws_manager.broadcast_market_state(symbol)
                    
                    # Process tick through bar aggregators to create candles
                    if symbol in self.bar_aggregators:
                        aggregator = self.bar_aggregators[symbol]
                        completed_bars = aggregator.process_tick(symbol, enriched_tick)
                        
                        # Broadcast ONLY completed candles (do not broadcast incomplete candles)
                        for interval, candle in completed_bars.items():
                            # Ensure candle has proper OHLC differences
                            if candle.get("high", 0) > candle.get("low", 0):
                                logger.info(
                                    f"[{symbol}] Candle completed: {interval} @ {candle.get('time', 0)} "
                                    f"OHLC: O={candle.get('open', 0):.5f} H={candle.get('high', 0):.5f} "
                                    f"L={candle.get('low', 0):.5f} C={candle.get('close', 0):.5f} "
                                    f"Ticks={candle.get('tick_count', 0)}"
                                )
                                await self.ws_manager.broadcast_candle(symbol, interval, candle)
                                # Update market state after candle completion
                                await self.ws_manager.broadcast_market_state(symbol)
                            else:
                                logger.warning(
                                    f"[{symbol}] Skipping invalid candle {interval}: "
                                    f"high={candle.get('high', 0):.5f} <= low={candle.get('low', 0):.5f}"
                                )
                else:
                    # No tick received or same tick
                    no_tick_count += 1
                    # Log warning if no ticks for a while (helps debug)
                    if no_tick_count == 1000:  # After ~10 seconds of no new ticks
                        logger.warning(f"[{symbol}] No new ticks received (polling every 10ms). Check if market is open or symbol is available.")
                        no_tick_count = 0  # Reset to avoid spam
                
                # Small delay to avoid excessive polling
                # 10ms = ~100 ticks/second max
                await asyncio.sleep(0.01)
                
        except asyncio.CancelledError:
            logger.info(f"[{symbol}] Tick stream cancelled")
        except Exception as e:
            logger.error(f"[{symbol}] Error in tick stream: {e}", exc_info=True)
            # Restart stream after error
            await asyncio.sleep(1)
            if symbol not in self.streams:  # Only restart if not manually stopped
                logger.info(f"[{symbol}] Attempting to restart stream after error...")
                await self.start_stream(symbol)
    
    async def stop_all(self):
        """Stop all tick streams."""
        symbols = list(self.streams.keys())
        for symbol in symbols:
            await self.stop_stream(symbol)
    
    def get_last_ticks(self, symbol: str, count: int = 100) -> list:
        """
        Get last N ticks for a symbol.
        
        Args:
            symbol: Symbol name
            count: Number of ticks to return
        
        Returns:
            List of tick dicts
        """
        if symbol not in self.last_ticks:
            return []
        ticks = list(self.last_ticks[symbol])
        return ticks[-count:] if len(ticks) > count else ticks


async def tick_stream(symbol: str, ws_manager, mt5_client: MT5Client):
    """
    Standalone tick streaming function.
    
    Args:
        symbol: Symbol to stream
        ws_manager: WebSocket manager
        mt5_client: MT5Client instance
    """
    streamer = TickStreamer(mt5_client, ws_manager)
    await streamer.start_stream(symbol)
    # Keep running until cancelled
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await streamer.stop_all()
