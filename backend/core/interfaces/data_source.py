"""
Data Source Interface Module

Defines the abstract interface that all FX data sources must implement.
This enables a plugin architecture where different data providers (MT5, Bloomberg,
Reuters, synthetic feeds, etc.) can be swapped interchangeably.

Design Decisions:
- Abstract base class ensures type safety and contract enforcement
- Async-first design for non-blocking operation
- Source ID enables tracking data provenance for arbitrage detection
- Health check method supports monitoring and fault tolerance
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class DataSourceConfig:
    """
    Configuration for a data source.
    
    Attributes:
        source_id: Unique identifier for this data source (e.g., "mt5_primary", "bloomberg_1")
        source_type: Type of data source (e.g., "mt5", "synthetic", "bloomberg")
        display_name: Human-readable name for UI display
        priority: Priority for arbitrage detection (lower = higher priority)
        latency_estimate_ms: Estimated network/processing latency in milliseconds
        reliability_score: Historical reliability score (0.0 to 1.0)
        symbols: List of symbols this source provides
        extra_config: Additional configuration specific to source type
    """
    source_id: str
    source_type: str
    display_name: str = ""
    priority: int = 1
    latency_estimate_ms: float = 0.0
    reliability_score: float = 1.0
    symbols: List[str] = field(default_factory=list)
    extra_config: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.display_name:
            self.display_name = f"{self.source_type.upper()} ({self.source_id})"


@dataclass
class RawTick:
    """
    Raw tick data from a data source.
    
    This is the format returned by get_tick() before normalization.
    Each data source plugin converts its native format to this structure.
    
    Attributes:
        symbol: Currency pair (e.g., "EURUSD")
        bid: Best bid price
        ask: Best ask price
        timestamp_ms: Unix timestamp in milliseconds
        source_id: Identifier of the data source
        volume: Optional tick volume
        last: Optional last traded price
        broker_time: Optional broker timestamp as ISO string
        extra: Optional additional fields from the source
    """
    symbol: str
    bid: float
    ask: float
    timestamp_ms: int
    source_id: str
    volume: Optional[int] = None
    last: Optional[float] = None
    broker_time: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "timestamp_ms": self.timestamp_ms,
            "source_id": self.source_id,
        }
        if self.volume is not None:
            result["volume"] = self.volume
        if self.last is not None:
            result["last"] = self.last
        if self.broker_time is not None:
            result["broker_time"] = self.broker_time
        if self.extra is not None:
            result["extra"] = self.extra
        return result


class DataSourceInterface(ABC):
    """
    Abstract interface for FX data sources.
    
    All data source plugins must implement this interface to participate
    in the arbitrage detection system. This ensures:
    - Consistent data format across all sources
    - Pluggable architecture for adding new data providers
    - Source tracking for arbitrage opportunity attribution
    
    Implementation Guidelines:
    1. connect() should handle reconnection logic internally
    2. get_tick() should be non-blocking and return quickly
    3. Source ID should be unique across all active sources
    4. is_healthy() should reflect real-time connection state
    """
    
    def __init__(self, config: DataSourceConfig):
        """
        Initialize the data source with configuration.
        
        Args:
            config: Data source configuration
        """
        self._config = config
        self._is_connected = False
        self._last_tick_time: Optional[int] = None
        self._tick_count = 0
        self._error_count = 0
    
    @property
    def source_id(self) -> str:
        """Get the unique source identifier."""
        return self._config.source_id
    
    @property
    def source_type(self) -> str:
        """Get the source type."""
        return self._config.source_type
    
    @property
    def config(self) -> DataSourceConfig:
        """Get the source configuration."""
        return self._config
    
    @property
    def is_connected(self) -> bool:
        """Check if the source is currently connected."""
        return self._is_connected
    
    @abstractmethod
    def connect(self) -> bool:
        """
        Establish connection to the data source.
        
        Should handle:
        - Initial connection establishment
        - Reconnection after disconnection
        - Error logging and state management
        
        Returns:
            True if connected successfully, False otherwise
        """
        pass
    
    @abstractmethod
    def disconnect(self) -> None:
        """
        Disconnect from the data source.
        
        Should:
        - Cleanly close connections
        - Release resources
        - Update internal state
        """
        pass
    
    @abstractmethod
    def get_tick(self, symbol: str) -> Optional[RawTick]:
        """
        Get the latest tick for a symbol.
        
        This method should be fast and non-blocking. If no new tick
        is available, return None rather than blocking.
        
        Args:
            symbol: Currency pair symbol (e.g., "EURUSD")
        
        Returns:
            RawTick with current price data, or None if unavailable
        """
        pass
    
    @abstractmethod
    def is_healthy(self) -> bool:
        """
        Check if the data source is healthy and producing data.
        
        Should check:
        - Connection is active
        - Data is being received (not stale)
        - No critical errors
        
        Returns:
            True if healthy, False otherwise
        """
        pass
    
    @abstractmethod
    def get_supported_symbols(self) -> List[str]:
        """
        Get list of symbols supported by this data source.
        
        Returns:
            List of symbol strings (e.g., ["EURUSD", "GBPUSD", "USDJPY"])
        """
        pass
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about this data source.
        
        Returns:
            Dictionary with tick count, error count, last tick time, etc.
        """
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "is_connected": self._is_connected,
            "tick_count": self._tick_count,
            "error_count": self._error_count,
            "last_tick_time": self._last_tick_time,
            "latency_estimate_ms": self._config.latency_estimate_ms,
            "reliability_score": self._config.reliability_score,
        }
    
    def _record_tick(self, timestamp_ms: int) -> None:
        """Record a successful tick retrieval."""
        self._last_tick_time = timestamp_ms
        self._tick_count += 1
    
    def _record_error(self) -> None:
        """Record an error occurrence."""
        self._error_count += 1
    
    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
