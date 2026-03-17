"""
Normalized Tick Module

Provides a unified tick format and normalization logic for the arbitrage engine.
All ticks from any data source are converted to NormalizedTick before processing.

Design Decisions:
- Dataclass for immutability and type safety
- Mid price calculated at normalization time for consistency
- Session detection embedded in tick for temporal analysis
- Spread calculated once and cached for performance
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def detect_trading_session(timestamp_ms: int) -> str:
    """
    Detect trading session based on UTC time.
    
    Sessions (approximate UTC times):
    - Tokyo/Asia: 00:00 - 09:00 UTC
    - London/Europe: 07:00 - 16:00 UTC  
    - New York/Americas: 12:00 - 21:00 UTC
    - Sydney/Pacific: 21:00 - 06:00 UTC
    
    Note: Sessions overlap, so we return the primary active session.
    
    Args:
        timestamp_ms: Unix timestamp in milliseconds
    
    Returns:
        Session name: "tokyo", "london", "new_york", "sydney", or "closed"
    """
    dt = datetime.utcfromtimestamp(timestamp_ms / 1000.0)
    hour = dt.hour
    
    # Primary session based on liquidity center
    if 0 <= hour < 7:
        return "tokyo"
    elif 7 <= hour < 12:
        # London morning, overlaps with Tokyo end
        return "london"
    elif 12 <= hour < 16:
        # London/NY overlap - highest liquidity
        return "london_ny"
    elif 16 <= hour < 21:
        return "new_york"
    else:
        # 21:00 - 00:00 - Sydney open, low liquidity
        return "sydney"


@dataclass(frozen=True)
class NormalizedTick:
    """
    Unified tick format for the arbitrage detection engine.
    
    All data sources produce NormalizedTicks after processing through
    the TickNormalizer. This ensures consistent format for:
    - Time alignment engine
    - Arbitrage detection logic
    - WebSocket broadcasting
    
    Attributes:
        symbol: Currency pair (e.g., "EURUSD")
        bid: Best bid price
        ask: Best ask price
        mid: Mid price = (bid + ask) / 2
        spread: Spread = ask - bid
        timestamp_ms: Unix timestamp in milliseconds
        source_id: Identifier of the originating data source
        session: Trading session at tick time
        volume: Optional tick volume
        latency_adjusted_time_ms: Timestamp adjusted for estimated source latency
    """
    symbol: str
    bid: float
    ask: float
    mid: float
    spread: float
    timestamp_ms: int
    source_id: str
    session: str
    volume: Optional[int] = None
    latency_adjusted_time_ms: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "mid": self.mid,
            "spread": self.spread,
            "timestamp_ms": self.timestamp_ms,
            "source_id": self.source_id,
            "session": self.session,
        }
        if self.volume is not None:
            result["volume"] = self.volume
        if self.latency_adjusted_time_ms is not None:
            result["latency_adjusted_time_ms"] = self.latency_adjusted_time_ms
        return result
    
    def spread_pips(self, pip_value: float = 0.0001) -> float:
        """
        Get spread in pips.
        
        Args:
            pip_value: Value of one pip (0.0001 for most pairs, 0.01 for JPY pairs)
        
        Returns:
            Spread in pips
        """
        # Detect JPY pairs and adjust pip value
        if "JPY" in self.symbol:
            pip_value = 0.01
        return self.spread / pip_value
    
    def is_stale(self, max_age_ms: int = 5000) -> bool:
        """
        Check if tick is stale based on current time.
        
        Args:
            max_age_ms: Maximum age in milliseconds before considered stale
        
        Returns:
            True if tick is older than max_age_ms
        """
        current_time_ms = int(datetime.utcnow().timestamp() * 1000)
        return (current_time_ms - self.timestamp_ms) > max_age_ms


class TickNormalizer:
    """
    Converts raw ticks from any data source into NormalizedTicks.
    
    Handles:
    - Mid price calculation
    - Spread calculation  
    - Session detection
    - Latency adjustment based on source configuration
    - Data validation
    """
    
    def __init__(self, source_latencies: Optional[Dict[str, float]] = None):
        """
        Initialize the normalizer.
        
        Args:
            source_latencies: Dict mapping source_id to estimated latency in ms
        """
        self._source_latencies = source_latencies or {}
        self._normalized_count = 0
        self._error_count = 0
    
    def set_source_latency(self, source_id: str, latency_ms: float) -> None:
        """
        Set or update latency estimate for a source.
        
        Args:
            source_id: Data source identifier
            latency_ms: Estimated latency in milliseconds
        """
        self._source_latencies[source_id] = latency_ms
    
    def normalize(self, raw_tick: 'RawTick') -> Optional[NormalizedTick]:
        """
        Convert a raw tick to a normalized tick.
        
        Args:
            raw_tick: Raw tick from a data source
        
        Returns:
            NormalizedTick or None if validation fails
        """
        from backend.core.interfaces.data_source import RawTick
        
        try:
            # Validate required fields
            if not raw_tick.symbol or raw_tick.bid <= 0 or raw_tick.ask <= 0:
                logger.warning(f"Invalid tick data: {raw_tick}")
                self._error_count += 1
                return None
            
            # Validate bid/ask relationship
            if raw_tick.bid > raw_tick.ask:
                logger.warning(f"Bid > Ask in tick: {raw_tick}")
                self._error_count += 1
                return None
            
            # Calculate derived fields
            mid = (raw_tick.bid + raw_tick.ask) / 2.0
            spread = raw_tick.ask - raw_tick.bid
            session = detect_trading_session(raw_tick.timestamp_ms)
            
            # Apply latency adjustment if known
            latency_ms = self._source_latencies.get(raw_tick.source_id, 0.0)
            latency_adjusted_time = None
            if latency_ms > 0:
                latency_adjusted_time = raw_tick.timestamp_ms - int(latency_ms)
            
            # Create normalized tick
            normalized = NormalizedTick(
                symbol=raw_tick.symbol.upper(),
                bid=raw_tick.bid,
                ask=raw_tick.ask,
                mid=mid,
                spread=spread,
                timestamp_ms=raw_tick.timestamp_ms,
                source_id=raw_tick.source_id,
                session=session,
                volume=raw_tick.volume,
                latency_adjusted_time_ms=latency_adjusted_time,
            )
            
            self._normalized_count += 1
            return normalized
            
        except Exception as e:
            logger.error(f"Error normalizing tick: {e}")
            self._error_count += 1
            return None
    
    def normalize_batch(self, raw_ticks: List['RawTick']) -> List[NormalizedTick]:
        """
        Normalize a batch of raw ticks.
        
        Args:
            raw_ticks: List of raw ticks
        
        Returns:
            List of successfully normalized ticks (failures are filtered out)
        """
        normalized = []
        for raw_tick in raw_ticks:
            tick = self.normalize(raw_tick)
            if tick is not None:
                normalized.append(tick)
        return normalized
    
    def get_stats(self) -> Dict[str, Any]:
        """Get normalization statistics."""
        return {
            "normalized_count": self._normalized_count,
            "error_count": self._error_count,
            "source_latencies": self._source_latencies.copy(),
        }
