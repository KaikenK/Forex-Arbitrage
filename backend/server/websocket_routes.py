"""
WebSocket Routes Module

Handles WebSocket connections for tick, candle, market state, and arbitrage streaming.
Manages client connections and broadcasts updates.

Extended for Arbitrage Detection Engine:
- Added arbitrage channel for streaming detected opportunities
- Added source comparison broadcasting
- Maintains backward compatibility with existing endpoints
"""

import asyncio
import json
import logging
import time
from typing import Dict, Set, Any, Optional, List
from fastapi import WebSocket, WebSocketDisconnect
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


class WebSocketManager:
    """
    Manages WebSocket connections and broadcasts.
    Supports ticks, candles, aggregated market state, and arbitrage channels.
    
    Extended for arbitrage detection:
    - /ws/arbitrage: Stream all arbitrage opportunities
    - /ws/arbitrage/{symbol}: Stream opportunities for specific symbol
    - /ws/sources: Stream source comparison data
    """
    
    def __init__(self):
        # Track connections per channel: {channel: set(websocket)}
        self.connections: Dict[str, Set[WebSocket]] = defaultdict(set)
        # Track aggregators per symbol/interval
        self.aggregators: Dict[str, Any] = {}
        # Track last tick per symbol for market state
        self.last_ticks: Dict[str, Dict[str, Any]] = {}
        # Track last candles per symbol/interval for market state
        self.last_candles: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
        
        # === Arbitrage-specific tracking ===
        # Track last ticks per source per symbol: {symbol: {source_id: tick}}
        self.last_ticks_by_source: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
        # Recent arbitrage opportunities (rolling buffer)
        self.recent_arbitrage: deque = deque(maxlen=100)
        # Arbitrage stats
        self.arbitrage_stats: Dict[str, Any] = {
            "total_opportunities": 0,
            "opportunities_by_type": defaultdict(int),
            "last_opportunity_time": None,
        }
    
    async def connect(self, websocket: WebSocket, channel: str):
        """
        Connect a WebSocket to a channel.
        
        Args:
            websocket: WebSocket connection
            channel: Channel name (e.g., "ticks_EURUSD", "candles_EURUSD_1s", "market_EURUSD")
        """
        await websocket.accept()
        self.connections[channel].add(websocket)
        logger.info(f"WebSocket connected to channel: {channel} (total: {len(self.connections[channel])})")
    
    def disconnect(self, websocket: WebSocket, channel: str):
        """
        Disconnect a WebSocket from a channel.
        
        Args:
            websocket: WebSocket connection
            channel: Channel name
        """
        self.connections[channel].discard(websocket)
        if not self.connections[channel]:
            del self.connections[channel]
        logger.info(f"WebSocket disconnected from channel: {channel}")
    
    async def broadcast_to_dashboard(self, msg_type: str, data: Dict[str, Any]):
        """
        Broadcast a message to all dashboard clients.
        
        Args:
            msg_type: Message type (tick, arbitrage, state_update, etc.)
            data: Data to include in the message
        """
        channel = "dashboard:unified"
        if channel not in self.connections:
            return
        
        message = json.dumps({"type": msg_type, **data})
        
        disconnected = set()
        # Iterate over a copy to avoid "Set changed size during iteration" error
        for websocket in list(self.connections[channel]):
            try:
                await websocket.send_text(message)
            except Exception as e:
                logger.warning(f"Error sending to dashboard: {e}")
                disconnected.add(websocket)
        
        for ws in disconnected:
            self.disconnect(ws, channel)
    
    async def broadcast_state_update(self, states: Dict[str, Any]):
        """
        Broadcast state machine states to dashboard clients.
        
        Args:
            states: Dict of symbol -> state info from ArbitrageStateMachine.get_all_states()
        """
        await self.broadcast_to_dashboard("state_update", {"states": states})
    
    async def broadcast_tick(self, symbol: str, tick: Dict[str, Any]):
        """
        Broadcast enriched tick to all connected clients for symbol.
        
        Args:
            symbol: Symbol name
            tick: Enriched tick data dict
        """
        channel = f"ticks_{symbol}"
        
        # Store last tick for market state
        self.last_ticks[symbol] = tick
        
        # Format as line-delimited JSON
        message = json.dumps(tick) + "\n"
        
        # Broadcast to ticks channel
        if channel in self.connections:
            disconnected = set()
            for websocket in list(self.connections[channel]):
                try:
                    await websocket.send_text(message)
                except Exception as e:
                    logger.warning(f"Error sending tick to client: {e}")
                    disconnected.add(websocket)
            
            # Remove disconnected clients
            for ws in disconnected:
                self.disconnect(ws, channel)
        
        # Also broadcast to dashboard with field mapping for frontend compatibility
        dashboard_tick = {
            **tick,
            "source": tick.get("source_id", "unknown"),  # Frontend expects 'source'
            "timestamp": tick.get("time_ms") or tick.get("time"),  # Frontend expects 'timestamp'
        }
        await self.broadcast_to_dashboard("tick", dashboard_tick)
    
    async def broadcast_candle(self, symbol: str, interval: str, candle: Dict[str, Any]):
        """
        Broadcast candle to all connected clients for symbol/interval.
        
        Args:
            symbol: Symbol name
            interval: Interval string (e.g., "1s", "5s", "1m")
            candle: Candle data dict
        """
        channel = f"candles_{symbol}_{interval}"
        
        # Store last candle for market state
        self.last_candles[symbol][interval] = candle
        
        if channel not in self.connections:
            return
        
        # Format as line-delimited JSON
        message = json.dumps(candle) + "\n"
        
        # Broadcast to all connections
        disconnected = set()
        for websocket in list(self.connections[channel]):
            try:
                await websocket.send_text(message)
            except Exception as e:
                logger.warning(f"Error sending candle to client: {e}")
                disconnected.add(websocket)
        
        # Remove disconnected clients
        for ws in disconnected:
            self.disconnect(ws, channel)
    
    def _calculate_market_state(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Calculate aggregated market state for a symbol.
        
        Args:
            symbol: Symbol name
        
        Returns:
            Market state dict or None if insufficient data
        """
        if symbol not in self.last_ticks:
            return None
        
        last_tick = self.last_ticks[symbol]
        last_candles = self.last_candles.get(symbol, {})
        
        # Get last candles for key intervals
        candle_1s = last_candles.get("1s")
        candle_1m = last_candles.get("1m")
        
        # Calculate trend from direction bias and velocity
        direction_bias = last_tick.get("direction_bias", 0)
        velocity = last_tick.get("velocity", 0.0)
        
        if direction_bias > 0 and velocity > 0:
            trend = "bullish"
        elif direction_bias < 0 and velocity < 0:
            trend = "bearish"
        else:
            trend = "neutral"
        
        # Calculate momentum (normalized velocity, clamped to -1 to 1)
        # Assuming typical velocity range of -0.001 to 0.001 for major pairs
        momentum = max(-1.0, min(1.0, velocity * 1000.0))
        
        # Calculate volatility rank (0-1) based on 1m volatility
        vol_1m = last_tick.get("vol_1m", 0.0)
        # Assuming typical volatility range of 0 to 0.002
        volatility_rank = min(1.0, vol_1m / 0.002) if vol_1m > 0 else 0.0
        
        # Build market state
        market_state = {
            "type": "market_state",
            "symbol": symbol,
            "spread": last_tick.get("spread", 0.0),
            "trend": trend,
            "momentum": momentum,
            "volatility_rank": volatility_rank,
            "last_tick": last_tick,
            "last_candle_1s": candle_1s,
            "last_candle_1m": candle_1m
        }
        
        return market_state
    
    async def broadcast_market_state(self, symbol: str):
        """
        Broadcast aggregated market state to all connected clients for symbol.
        
        Args:
            symbol: Symbol name
        """
        channel = f"market_{symbol}"
        
        if channel not in self.connections:
            return
        
        market_state = self._calculate_market_state(symbol)
        if not market_state:
            return
        
        # Format as line-delimited JSON
        message = json.dumps(market_state) + "\n"
        
        # Broadcast to all connections
        disconnected = set()
        for websocket in list(self.connections[channel]):
            try:
                await websocket.send_text(message)
            except Exception as e:
                logger.warning(f"Error sending market state to client: {e}")
                disconnected.add(websocket)
        
        # Remove disconnected clients
        for ws in disconnected:
            self.disconnect(ws, channel)
    
    def get_connection_count(self, channel: str) -> int:
        """Get number of connections for a channel."""
        return len(self.connections.get(channel, set()))
    
    def get_all_channels(self) -> list:
        """Get list of all active channels."""
        return list(self.connections.keys())
    
    # =========================================================================
    # ARBITRAGE BROADCASTING METHODS
    # =========================================================================
    
    async def broadcast_arbitrage(self, opportunity: Dict[str, Any]):
        """
        Broadcast an arbitrage opportunity to connected clients.
        
        Broadcasts to:
        - Global arbitrage channel: "arbitrage"
        - Symbol-specific channels: "arbitrage_EURUSD", etc.
        - Dashboard channel for unified view
        
        Args:
            opportunity: Ranked opportunity dict with scores
        """
        # Track stats
        self.arbitrage_stats["total_opportunities"] += 1
        self.arbitrage_stats["last_opportunity_time"] = int(time.time() * 1000)
        
        opp_data = opportunity.get("opportunity", opportunity)
        opp_type = opp_data.get("type", "unknown")
        self.arbitrage_stats["opportunities_by_type"][opp_type] += 1
        
        # Add to recent buffer
        self.recent_arbitrage.append(opportunity)
        
        # Format message
        message = json.dumps(opportunity) + "\n"
        
        # Broadcast to global arbitrage channel
        await self._broadcast_to_channel("arbitrage", message)
        
        # Broadcast to symbol-specific channels
        symbols = opp_data.get("symbols", [])
        for symbol in symbols:
            await self._broadcast_to_channel(f"arbitrage_{symbol}", message)
        
        # Also broadcast to dashboard
        await self.broadcast_to_dashboard("arbitrage", {"opportunities": [opportunity]})
    
    async def broadcast_source_comparison(self, symbol: str, comparison: Dict[str, Any]):
        """
        Broadcast source comparison data for a symbol.
        
        Shows price differences between sources for the same symbol.
        
        Args:
            symbol: Currency pair symbol
            comparison: Comparison data dict
        """
        channel = f"sources_{symbol}"
        message = json.dumps(comparison) + "\n"
        await self._broadcast_to_channel(channel, message)
    
    async def _broadcast_to_channel(self, channel: str, message: str):
        """
        Broadcast a message to all clients on a channel.
        
        Args:
            channel: Channel name
            message: JSON message string
        """
        if channel not in self.connections:
            return
        
        disconnected = set()
        for websocket in list(self.connections[channel]):
            try:
                await websocket.send_text(message)
            except Exception as e:
                logger.warning(f"Error sending to {channel}: {e}")
                disconnected.add(websocket)
        
        for ws in disconnected:
            self.disconnect(ws, channel)
    
    def update_tick_by_source(self, symbol: str, source_id: str, tick: Dict[str, Any]):
        """
        Update tracked tick for a source/symbol combination.
        
        Used for source comparison and arbitrage display.
        
        Args:
            symbol: Currency pair
            source_id: Data source identifier
            tick: Tick data dict
        """
        self.last_ticks_by_source[symbol][source_id] = tick
    
    def get_source_comparison(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get price comparison across sources for a symbol.
        
        Args:
            symbol: Currency pair
        
        Returns:
            Comparison dict with prices per source and differences
        """
        ticks = self.last_ticks_by_source.get(symbol, {})
        if len(ticks) < 2:
            return None
        
        sources = []
        mids = []
        
        for source_id, tick in ticks.items():
            mid = tick.get("mid") or (tick.get("bid", 0) + tick.get("ask", 0)) / 2
            sources.append({
                "source_id": source_id,
                "bid": tick.get("bid"),
                "ask": tick.get("ask"),
                "mid": mid,
                "spread": tick.get("spread"),
                "time": tick.get("time"),
            })
            mids.append(mid)
        
        # Calculate differences
        max_mid = max(mids)
        min_mid = min(mids)
        pip_value = 0.01 if "JPY" in symbol else 0.0001
        
        return {
            "symbol": symbol,
            "sources": sources,
            "max_mid": max_mid,
            "min_mid": min_mid,
            "mid_difference": max_mid - min_mid,
            "mid_difference_pips": (max_mid - min_mid) / pip_value,
            "timestamp": int(time.time() * 1000),
        }
    
    def get_recent_arbitrage(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get recent arbitrage opportunities.
        
        Args:
            limit: Maximum number to return
        
        Returns:
            List of recent opportunities (newest first)
        """
        recent = list(self.recent_arbitrage)
        recent.reverse()
        return recent[:limit]
    
    def get_arbitrage_stats(self) -> Dict[str, Any]:
        """Get arbitrage statistics."""
        return {
            "total_opportunities": self.arbitrage_stats["total_opportunities"],
            "opportunities_by_type": dict(self.arbitrage_stats["opportunities_by_type"]),
            "last_opportunity_time": self.arbitrage_stats["last_opportunity_time"],
            "buffer_size": len(self.recent_arbitrage),
        }
    async def _redis_listener(self):
        """Background task to listen to Redis channels and broadcast."""
        from backend.core.redis_client import redis_client
        
        async def handle_scored_opp(msg: Dict[str, Any]):
            print(f"WS Manager received scored opp!", flush=True)
            await self.broadcast_arbitrage(msg)
            
        async def handle_orderbook(msg: Dict[str, Any]):
            # Broadcast to unified dashboard directly
            if "dashboard:unified" in self.connections:
                message = json.dumps({"type": "orderbook", "data": msg}) + "\n"
                await self._broadcast_to_channel("dashboard:unified", message)
            
        try:
            await redis_client.subscribe_many({
                "arbex.scored_opps": handle_scored_opp,
                "arbex.orderbooks": handle_orderbook
            })
            # Keep listener alive
            while True:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Redis listener failed: {e}")

    def start_background_tasks(self):
        """Start all background tasks for WebSocketManager."""
        asyncio.create_task(self._redis_listener())


# Global WebSocket manager instance
ws_manager = WebSocketManager()


async def websocket_tick_endpoint(websocket: WebSocket, symbol: str):
    """
    WebSocket endpoint for tick streaming.
    
    Args:
        websocket: WebSocket connection
        symbol: Symbol to stream (e.g., "EURUSD")
    """
    channel = f"ticks_{symbol}"
    
    try:
        await ws_manager.connect(websocket, channel)
        logger.info(f"Client connected to tick stream: {symbol}")
        
        # Send last tick if available (catch-up)
        if symbol in ws_manager.last_ticks:
            last_tick = ws_manager.last_ticks[symbol]
            await websocket.send_text(json.dumps(last_tick) + "\n")
        
        # Keep connection alive and handle disconnects
        while True:
            try:
                # Wait for ping or disconnect
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # Echo ping back as pong
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                await websocket.send_text("ping")
            except WebSocketDisconnect:
                break
                
    except WebSocketDisconnect:
        logger.info(f"Client disconnected from tick stream: {symbol}")
    except Exception as e:
        logger.error(f"Error in tick WebSocket endpoint for {symbol}: {e}")
    finally:
        ws_manager.disconnect(websocket, channel)


async def websocket_candle_endpoint(websocket: WebSocket, symbol: str, interval: str):
    """
    WebSocket endpoint for candle streaming.
    
    Args:
        websocket: WebSocket connection
        symbol: Symbol to stream (e.g., "EURUSD")
        interval: Interval string (e.g., "1s", "5s", "15s", "1m")
    """
    channel = f"candles_{symbol}_{interval}"
    
    try:
        await ws_manager.connect(websocket, channel)
        logger.info(f"Client connected to candle stream: {symbol} @ {interval}")
        
        # Send last candle if available (catch-up)
        if symbol in ws_manager.last_candles and interval in ws_manager.last_candles[symbol]:
            last_candle = ws_manager.last_candles[symbol][interval]
            await websocket.send_text(json.dumps(last_candle) + "\n")
        
        # Keep connection alive and handle disconnects
        while True:
            try:
                # Wait for ping or disconnect
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # Echo ping back as pong
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                await websocket.send_text("ping")
            except WebSocketDisconnect:
                break
                
    except WebSocketDisconnect:
        logger.info(f"Client disconnected from candle stream: {symbol} @ {interval}")
    except Exception as e:
        logger.error(f"Error in candle WebSocket endpoint for {symbol} @ {interval}: {e}")
    finally:
        ws_manager.disconnect(websocket, channel)


async def websocket_market_state_endpoint(websocket: WebSocket, symbol: str):
    """
    WebSocket endpoint for aggregated market state streaming.
    
    Args:
        websocket: WebSocket connection
        symbol: Symbol to stream (e.g., "EURUSD")
    """
    channel = f"market_{symbol}"
    
    try:
        await ws_manager.connect(websocket, channel)
        logger.info(f"Client connected to market state stream: {symbol}")
        
        # Send initial market state if available
        market_state = ws_manager._calculate_market_state(symbol)
        if market_state:
            await websocket.send_text(json.dumps(market_state) + "\n")
        
        # Keep connection alive and handle disconnects
        # Market state updates are triggered by tick/candle broadcasts
        while True:
            try:
                # Wait for ping or disconnect
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # Echo ping back as pong
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                await websocket.send_text("ping")
            except WebSocketDisconnect:
                break
                
    except WebSocketDisconnect:
        logger.info(f"Client disconnected from market state stream: {symbol}")
    except Exception as e:
        logger.error(f"Error in market state WebSocket endpoint for {symbol}: {e}")
    finally:
        ws_manager.disconnect(websocket, channel)


# =========================================================================
# ARBITRAGE WEBSOCKET ENDPOINTS
# =========================================================================

async def websocket_arbitrage_endpoint(websocket: WebSocket, symbol: Optional[str] = None):
    """
    WebSocket endpoint for arbitrage opportunity streaming.
    
    Streams detected arbitrage opportunities in real-time.
    If symbol is provided, only streams opportunities for that symbol.
    
    Args:
        websocket: WebSocket connection
        symbol: Optional symbol filter (e.g., "EURUSD")
    
    Message format:
        {
            "opportunity": {
                "type": "cross_source",
                "symbols": ["EURUSD"],
                "sources": ["mt5_primary", "synthetic_bloomberg"],
                "buy_source": "synthetic_bloomberg",
                "sell_source": "mt5_primary",
                "buy_price": 1.0850,
                "sell_price": 1.0852,
                "estimated_profit_pips": 0.2,
                "confidence_score": 0.75,
                ...
            },
            "composite_score": 65.5,
            "rank": 1,
            ...
        }
    """
    channel = f"arbitrage_{symbol}" if symbol else "arbitrage"
    
    try:
        await ws_manager.connect(websocket, channel)
        logger.info(f"Client connected to arbitrage stream: {symbol or 'all'}")
        
        # Send recent opportunities as catch-up
        recent = ws_manager.get_recent_arbitrage(limit=10)
        if recent:
            # Filter by symbol if specified
            if symbol:
                recent = [
                    opp for opp in recent 
                    if symbol in opp.get("opportunity", {}).get("symbols", [])
                ]
            
            for opp in recent:
                await websocket.send_text(json.dumps(opp) + "\n")
        
        # Keep connection alive
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_text("ping")
            except WebSocketDisconnect:
                break
                
    except WebSocketDisconnect:
        logger.info(f"Client disconnected from arbitrage stream: {symbol or 'all'}")
    except Exception as e:
        logger.error(f"Error in arbitrage WebSocket endpoint: {e}")
    finally:
        ws_manager.disconnect(websocket, channel)


async def websocket_sources_endpoint(websocket: WebSocket, symbol: str):
    """
    WebSocket endpoint for source comparison streaming.
    
    Streams real-time price comparisons across data sources for a symbol.
    Useful for monitoring price discrepancies between sources.
    
    Args:
        websocket: WebSocket connection
        symbol: Symbol to compare (e.g., "EURUSD")
    
    Message format:
        {
            "symbol": "EURUSD",
            "sources": [
                {"source_id": "mt5_primary", "bid": 1.0850, "ask": 1.0851, ...},
                {"source_id": "synthetic_bloomberg", "bid": 1.0849, "ask": 1.0852, ...}
            ],
            "mid_difference_pips": 0.5,
            ...
        }
    """
    channel = f"sources_{symbol}"
    
    try:
        await ws_manager.connect(websocket, channel)
        logger.info(f"Client connected to source comparison stream: {symbol}")
        
        # Send initial comparison if available
        comparison = ws_manager.get_source_comparison(symbol)
        if comparison:
            await websocket.send_text(json.dumps(comparison) + "\n")
        
        # Keep connection alive
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_text("ping")
            except WebSocketDisconnect:
                break
                
    except WebSocketDisconnect:
        logger.info(f"Client disconnected from source comparison stream: {symbol}")
    except Exception as e:
        logger.error(f"Error in source comparison WebSocket endpoint for {symbol}: {e}")
    finally:
        ws_manager.disconnect(websocket, channel)
