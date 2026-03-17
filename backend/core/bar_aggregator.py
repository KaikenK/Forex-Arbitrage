"""
Bar Aggregator Module

Builds candles (bars) from tick data with VWAP, volatility, and velocity metrics.
Supports custom intervals including micro-candles: 100ms, 500ms, 1s, 5s, 15s, 1m.
"""

import logging
import statistics
from typing import Dict, Any, Optional
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class BarAggregator:
    """
    Aggregates ticks into candles (bars) with configurable intervals.
    Includes VWAP, weighted volume, volatility, and velocity calculations.
    """
    
    # Interval definitions in seconds (micro-candles use fractional seconds)
    INTERVALS = {
        "100ms": 0.1,
        "500ms": 0.5,
        "1s": 1,
        "5s": 5,
        "15s": 15,
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "1h": 3600
    }
    
    def __init__(self, interval: str, ws_manager=None):
        """
        Initialize bar aggregator.
        
        Args:
            interval: Interval string (e.g., "100ms", "500ms", "1s", "5s", "15s", "1m")
            ws_manager: WebSocket manager for broadcasting candles
        """
        if interval not in self.INTERVALS:
            raise ValueError(f"Invalid interval: {interval}. Valid: {list(self.INTERVALS.keys())}")
        
        self.interval = interval
        self.interval_seconds = self.INTERVALS[interval]
        self.ws_manager = ws_manager
        
        # Store current bars per symbol: {symbol: bar_dict}
        self.current_bars = {}
        
        # Store last completed bars per symbol: {symbol: bar_dict}
        self.last_completed_bars = {}
        
        # Store tick history per symbol for volatility calculation: {symbol: list of prices}
        self.tick_history = defaultdict(list)
    
    def _calculate_vwap(self, prices: list, volumes: list) -> float:
        """
        Calculate Volume Weighted Average Price.
        
        Args:
            prices: List of prices
            volumes: List of volumes (can be tick counts)
        
        Returns:
            VWAP value
        """
        if not prices or not volumes or len(prices) != len(volumes):
            return 0.0
        
        total_value = sum(p * v for p, v in zip(prices, volumes))
        total_volume = sum(volumes)
        
        return total_value / total_volume if total_volume > 0 else 0.0
    
    def _calculate_volatility(self, prices: list) -> float:
        """
        Calculate volatility (standard deviation) of prices in current candle.
        
        Args:
            prices: List of prices
        
        Returns:
            Volatility (stddev) or 0.0 if insufficient data
        """
        if len(prices) < 2:
            return 0.0
        return float(statistics.stdev(prices))
    
    def _calculate_velocity(self, open_price: float, close_price: float) -> float:
        """
        Calculate candle velocity (price change normalized by interval duration).
        
        Args:
            open_price: Opening price
            close_price: Closing price
        
        Returns:
            Velocity (price change per second)
        """
        price_change = close_price - open_price
        return price_change / self.interval_seconds if self.interval_seconds > 0 else price_change
    
    def process_tick(self, symbol: str, tick: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Process a tick and update/create bar.
        
        Args:
            symbol: Symbol name
            tick: Enriched tick data dict with "time", "bid", "ask", "mid", etc.
        
        Returns:
            Completed bar dict if bar just closed, None otherwise
        """
        # Get time from tick (enriched tick uses "time" in milliseconds)
        tick_time_ms = tick.get("time_ms", tick.get("time", 0))
        if tick_time_ms == 0:
            logger.warning(f"Invalid tick time for {symbol}: {tick}")
            return None
        tick_time_sec = tick_time_ms / 1000.0  # Use float for micro-intervals
        
        # Calculate bar start time (rounded down to interval)
        bar_start_sec = (int(tick_time_sec / self.interval_seconds)) * self.interval_seconds
        bar_start_ms = int(bar_start_sec * 1000)
        
        # Use mid price from enriched tick if available, otherwise calculate
        if "mid" in tick:
            price = tick["mid"]
        else:
            price = (tick["bid"] + tick["ask"]) / 2.0
        
        # Get spread from tick
        spread = tick.get("spread", 0.0)
        
        # Get or create current bar for this symbol
        if symbol not in self.current_bars:
            # New bar
            self.current_bars[symbol] = {
                "time": bar_start_ms,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1,
                "tick_count": 1,
                "spreads": [spread],  # Track spreads for average
                "prices": [price],  # For VWAP calculation
                "volumes": [1]  # Tick count as volume
            }
            self.tick_history[symbol] = [price]
        else:
            current_bar = self.current_bars[symbol]
            
            # Check if we've moved to a new bar
            if bar_start_ms > current_bar["time"]:
                # Current bar is complete, calculate final metrics
                # Ensure we have proper OHLC differences for visible candles
                # Validate and fix OHLC structure
                open_price = current_bar["open"]
                close_price = current_bar["close"]
                high_price = current_bar["high"]
                low_price = current_bar["low"]
                
                # If high == low (no price movement), add minimum spread
                if high_price <= low_price:
                    min_spread = spread if spread > 0 else 0.00001
                    min_candle_height = max(min_spread, 0.00001)
                    # Center the candle around the close price
                    high_price = close_price + min_candle_height / 2
                    low_price = close_price - min_candle_height / 2
                
                # Ensure high is the maximum of open, close, and current high
                high_price = max(open_price, close_price, high_price)
                # Ensure low is the minimum of open, close, and current low
                low_price = min(open_price, close_price, low_price)
                
                # Update the bar with corrected values
                current_bar["high"] = high_price
                current_bar["low"] = low_price
                
                completed_bar = self._finalize_bar(symbol, current_bar)
                self.last_completed_bars[symbol] = completed_bar
                
                # Reset tick history for new bar
                self.tick_history[symbol] = []
                
                # Start new bar
                self.current_bars[symbol] = {
                    "time": bar_start_ms,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 1,
                    "tick_count": 1,
                    "spreads": [spread],
                    "prices": [price],
                    "volumes": [1]
                }
                self.tick_history[symbol] = [price]
                
                return completed_bar
            else:
                # Update current bar
                current_bar["high"] = max(current_bar["high"], price)
                current_bar["low"] = min(current_bar["low"], price)
                current_bar["close"] = price
                current_bar["volume"] += 1
                current_bar["tick_count"] = current_bar.get("tick_count", 0) + 1
                current_bar["spreads"].append(spread)
                current_bar["prices"].append(price)
                current_bar["volumes"].append(1)
                self.tick_history[symbol].append(price)
        
        return None
    
    def _finalize_bar(self, symbol: str, bar: Dict[str, Any]) -> Dict[str, Any]:
        """
        Finalize a completed bar with all calculated metrics.
        
        Args:
            symbol: Symbol name
            bar: Bar dict with OHLCV and price/volume lists
        
        Returns:
            Finalized bar dict with type, symbol, interval, VWAP, volatility, velocity
        """
        # Calculate VWAP
        vwap = self._calculate_vwap(bar["prices"], bar["volumes"])
        
        # Calculate volatility
        volatility = self._calculate_volatility(bar["prices"])
        
        # Calculate velocity (normalized by interval duration)
        velocity = self._calculate_velocity(bar["open"], bar["close"])
        
        # Calculate direction (+1 for bullish, -1 for bearish)
        direction = 1 if bar["close"] >= bar["open"] else -1
        
        # Calculate average spread
        spreads = bar.get("spreads", [])
        spread_avg = float(statistics.mean(spreads)) if len(spreads) > 0 else 0.0
        
        # Get tick count
        tick_count = bar.get("tick_count", bar["volume"])
        
        # Build finalized candle
        finalized = {
            "type": "candle",
            "symbol": symbol,
            "interval": self.interval,
            "time": bar["time"],  # Include time field
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar["volume"],
            "vwap": vwap,
            "direction": direction,
            "tick_count": tick_count,
            "spread_avg": spread_avg,
            "velocity": velocity
        }
        
        # Clean up internal fields
        bar.pop("prices", None)
        bar.pop("volumes", None)
        bar.pop("spreads", None)
        
        return finalized
    
    def get_current_bar(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get current (incomplete) bar for symbol with live metrics.
        
        Args:
            symbol: Symbol name
        
        Returns:
            Current bar dict with live VWAP, volatility, velocity or None
        """
        if symbol not in self.current_bars:
            return None
        
        bar = self.current_bars[symbol].copy()
        
        # Calculate live metrics
        if "prices" in bar and len(bar["prices"]) > 0:
            vwap = self._calculate_vwap(bar["prices"], bar["volumes"])
            volatility = self._calculate_volatility(bar["prices"])
            velocity = self._calculate_velocity(bar["open"], bar["close"])
            
            # Add metrics to bar
            bar["vwap"] = vwap
            bar["volatility"] = volatility
            bar["velocity"] = velocity
            
            # Remove internal fields
            bar.pop("prices", None)
            bar.pop("volumes", None)
            
            # Add type info
            bar["type"] = "candle"
            bar["symbol"] = symbol
            bar["interval"] = self.interval
        
        return bar
    
    def get_last_completed_bar(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get last completed bar for symbol.
        
        Args:
            symbol: Symbol name
        
        Returns:
            Last completed bar dict or None
        """
        return self.last_completed_bars.get(symbol)
    
    def reset(self, symbol: Optional[str] = None):
        """
        Reset aggregator state.
        
        Args:
            symbol: Symbol to reset (None for all)
        """
        if symbol:
            self.current_bars.pop(symbol, None)
            self.last_completed_bars.pop(symbol, None)
            self.tick_history.pop(symbol, None)
        else:
            self.current_bars.clear()
            self.last_completed_bars.clear()
            self.tick_history.clear()


class MultiIntervalAggregator:
    """
    Manages multiple bar aggregators for different intervals.
    """
    
    def __init__(self, intervals: list, ws_manager=None):
        """
        Initialize multi-interval aggregator.
        
        Args:
            intervals: List of interval strings (e.g., ["100ms", "500ms", "1s", "5s", "1m"])
            ws_manager: WebSocket manager
        """
        self.aggregators = {}
        self.ws_manager = ws_manager
        
        for interval in intervals:
            self.aggregators[interval] = BarAggregator(interval, ws_manager)
    
    def process_tick(self, symbol: str, tick: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """
        Process tick through all aggregators.
        
        Args:
            symbol: Symbol name
            tick: Enriched tick data
        
        Returns:
            Dict of completed bars: {interval: candle_dict}
        """
        completed_bars = {}
        
        for interval, aggregator in self.aggregators.items():
            completed = aggregator.process_tick(symbol, tick)
            if completed:
                completed_bars[interval] = completed
        
        return completed_bars
    
    def get_current_bars(self, symbol: str) -> Dict[str, Dict[str, Any]]:
        """Get current bars for all intervals."""
        return {
            interval: agg.get_current_bar(symbol)
            for interval, agg in self.aggregators.items()
        }
    
    def get_last_completed_bars(self, symbol: str) -> Dict[str, Dict[str, Any]]:
        """Get last completed bars for all intervals."""
        return {
            interval: agg.get_last_completed_bar(symbol)
            for interval, agg in self.aggregators.items()
        }
