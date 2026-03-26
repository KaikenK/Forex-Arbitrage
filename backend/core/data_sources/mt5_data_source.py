"""
MT5 Data Source Plugin

Wraps the existing MT5Client to implement the DataSourceInterface.
This enables MT5 to participate in the multi-source arbitrage detection
system alongside other data providers.

Design Decisions:
- Composition over inheritance: wraps MT5Client rather than modifying it
- Preserves all existing MT5Client functionality
- Adds source tracking and health monitoring
- Thread-safe tick retrieval
"""

import time
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

from backend.core.interfaces.data_source import (
    DataSourceInterface,
    DataSourceConfig,
    RawTick,
)
from backend.core.mt5_client import MT5Client

logger = logging.getLogger(__name__)


class MT5DataSource(DataSourceInterface):
    """
    MetaTrader 5 data source plugin.
    
    Wraps MT5Client to provide a standardized interface for the
    arbitrage detection engine. Supports both auto-attach mode
    (connecting to running MT5 terminal) and credential-based login.
    
    Example:
        config = DataSourceConfig(
            source_id="mt5_primary",
            source_type="mt5",
            display_name="MT5 Primary Feed",
            latency_estimate_ms=50.0,
            symbols=["EURUSD", "GBPUSD", "USDJPY"]
        )
        
        source = MT5DataSource(config)
        if source.connect():
            tick = source.get_tick("EURUSD")
            print(f"EURUSD: {tick.bid}/{tick.ask}")
    """
    
    def __init__(
        self,
        config: DataSourceConfig,
        path: Optional[str] = None,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
    ):
        """
        Initialize MT5 data source.
        
        Args:
            config: Data source configuration
            path: Path to MT5 terminal (None for auto-detect)
            login: Account login (None for auto-attach mode)
            password: Account password
            server: Broker server name
        """
        super().__init__(config)
        
        # Create underlying MT5 client
        self._mt5_client = MT5Client(
            path=path,
            login=login,
            password=password,
            server=server,
        )
        
        # Track symbol selection state
        self._selected_symbols: set = set()
        
        # Store credentials for reconnection
        self._path = path
        self._login = login
        self._password = password
        self._server = server
        
        # Health tracking
        self._last_successful_tick: Dict[str, int] = {}
        self._health_check_interval_ms = 5000  # 5 seconds
    
    @property
    def mt5_client(self) -> MT5Client:
        """
        Get the underlying MT5Client instance.
        
        This allows access to MT5-specific functionality not exposed
        through the generic DataSourceInterface, such as getting
        historical bars.
        """
        return self._mt5_client
    
    def connect(self) -> bool:
        """
        Connect to MetaTrader 5.
        
        Attempts to connect to MT5, either by attaching to an existing
        running terminal or by logging in with credentials.
        
        Returns:
            True if connected successfully
        """
        try:
            if self._mt5_client.connect():
                self._is_connected = True
                logger.info(f"[{self.source_id}] MT5 data source connected")
                
                # Pre-select configured symbols
                for symbol in self._config.symbols:
                    if self._mt5_client.select_symbol(symbol):
                        self._selected_symbols.add(symbol)
                        logger.info(f"[{self.source_id}] Symbol {symbol} selected")
                    else:
                        logger.warning(f"[{self.source_id}] Failed to select symbol {symbol}")
                
                return True
            else:
                self._is_connected = False
                logger.error(f"[{self.source_id}] Failed to connect to MT5")
                return False
                
        except Exception as e:
            logger.error(f"[{self.source_id}] MT5 connection error: {e}")
            self._is_connected = False
            self._record_error()
            return False
    
    def disconnect(self) -> None:
        """
        Disconnect from MetaTrader 5.
        
        Cleanly shuts down the MT5 connection and releases resources.
        """
        try:
            self._mt5_client.disconnect()
            self._is_connected = False
            self._selected_symbols.clear()
            logger.info(f"[{self.source_id}] MT5 data source disconnected")
        except Exception as e:
            logger.error(f"[{self.source_id}] Error disconnecting from MT5: {e}")
    
    def get_tick(self, symbol: str) -> Optional[RawTick]:
        """
        Get the latest tick for a symbol from MT5.
        
        Args:
            symbol: Currency pair symbol (e.g., "EURUSD")
        
        Returns:
            RawTick with current price data, or None if unavailable
        """
        if not self._is_connected:
            logger.warning(f"[{self.source_id}] Cannot get tick - not connected")
            return None
        
        try:
            # Ensure symbol is selected
            symbol = symbol.upper()
            if symbol not in self._selected_symbols:
                if self._mt5_client.select_symbol(symbol):
                    self._selected_symbols.add(symbol)
                else:
                    logger.warning(f"[{self.source_id}] Cannot select symbol {symbol}")
                    return None
            
            # Get tick from MT5
            mt5_tick = self._mt5_client.get_tick(symbol)
            
            if mt5_tick is None:
                return None
            
            # Convert to RawTick format
            raw_tick = RawTick(
                symbol=symbol,
                bid=mt5_tick["bid"],
                ask=mt5_tick["ask"],
                timestamp_ms=mt5_tick["time"],
                source_id=self.source_id,
                volume=mt5_tick.get("volume_real", mt5_tick.get("volume")),
                last=mt5_tick.get("last"),
                broker_time=mt5_tick.get("broker_time"),
            )
            
            # Record successful tick
            self._record_tick(raw_tick.timestamp_ms)
            self._last_successful_tick[symbol] = raw_tick.timestamp_ms
            
            return raw_tick
            
        except Exception as e:
            logger.error(f"[{self.source_id}] Error getting tick for {symbol}: {e}")
            self._record_error()
            return None
    
    def is_healthy(self) -> bool:
        """
        Check if the MT5 data source is healthy.
        
        Healthy means:
        - Connected to MT5
        - Receiving recent ticks (not stale)
        - Low error rate
        
        Returns:
            True if healthy
        """
        if not self._is_connected:
            return False
        
        # Check if MT5 client is connected
        if not self._mt5_client.is_connected:
            return False
        
        # Check if we have recent ticks (at least one in last 5 seconds)
        current_time_ms = int(time.time() * 1000)
        if self._last_tick_time:
            tick_age_ms = current_time_ms - self._last_tick_time
            if tick_age_ms > self._health_check_interval_ms:
                logger.warning(f"[{self.source_id}] Stale data - last tick {tick_age_ms}ms ago")
                return False
        
        return True
    
    def get_supported_symbols(self) -> List[str]:
        """
        Get list of symbols supported by this MT5 connection.
        
        Returns list from config, as MT5 may support more symbols
        but we only stream configured ones.
        
        Returns:
            List of symbol strings
        """
        return self._config.symbols.copy()
    
    def ensure_connected(self) -> bool:
        """
        Ensure MT5 is connected, attempting reconnection if needed.
        
        Returns:
            True if connected
        """
        if self._is_connected and self._mt5_client.is_connected:
            return True
        
        logger.info(f"[{self.source_id}] Attempting reconnection...")
        return self.connect()
    
    def get_bars(self, symbol: str, timeframe: int, count: int = 1000):
        """
        Get historical bars from MT5.
        
        This is a pass-through to the underlying MT5Client for
        backward compatibility with existing bar aggregation code.
        
        Args:
            symbol: Symbol name
            timeframe: MT5 timeframe constant
            count: Number of bars to retrieve
        
        Returns:
            List of bar dicts or None
        """
        return self._mt5_client.get_bars(symbol, timeframe, count)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get detailed statistics for this MT5 data source."""
        base_stats = super().get_stats()
        
        # Add MT5-specific stats
        base_stats.update({
            "selected_symbols": list(self._selected_symbols),
            "mt5_connected": self._mt5_client.is_connected if self._mt5_client else False,
            "last_ticks_per_symbol": self._last_successful_tick.copy(),
        })
        
        return base_stats
