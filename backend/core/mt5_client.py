"""
MetaTrader 5 Client Module

Handles MT5 connection, initialization, and data retrieval.
Provides clean interface for tick and bar data access.
"""

import MetaTrader5 as mt5
import logging
import time
from typing import Optional, Dict, List, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class MT5Client:
    """
    MetaTrader 5 client wrapper.
    
    Handles connection, reconnection, and data retrieval.
    """
    
    def __init__(self, path: Optional[str] = None, login: Optional[int] = None, 
                 password: Optional[str] = None, server: Optional[str] = None):
        """
        Initialize MT5 client.
        
        Args:
            path: Path to MT5 terminal (None for auto-detect)
            login: Account login (None for demo)
            password: Account password (None for demo)
            server: Server name (None for auto)
        """
        self.path = path
        self.login = login
        self.password = password
        self.server = server
        self.is_connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 5  # seconds
        
    def connect(self) -> bool:
        """
        Connect to MetaTrader 5.
        
        If login/password/server are None, attaches to existing MT5 terminal session.
        Otherwise, performs login with provided credentials.
        
        Returns:
            True if connected successfully, False otherwise
        """
        try:
            # Initialize MT5
            # If path is None, don't pass it (MT5 will auto-detect)
            if self.path is None:
                initialized = mt5.initialize()
            else:
                initialized = mt5.initialize(path=self.path)
            
            if not initialized:
                error = mt5.last_error()
                logger.error(f"MT5 initialization failed: {error}")
                return False
            
            logger.info("MT5 initialized successfully")
            
            # If no login details are provided, attach to existing terminal session
            if self.login is None or self.password is None or self.server is None:
                logger.info("No login credentials provided — attaching to existing MT5 session")
                
                # Verify we can get account info (confirms connection to terminal)
                account_info = mt5.account_info()
                if account_info is None:
                    logger.warning("Could not get account info, but MT5 is initialized")
                    # Still consider connected if initialize() succeeded
                    self.is_connected = True
                    self.reconnect_attempts = 0
                    return True
                
                self.is_connected = True
                self.reconnect_attempts = 0
                
                logger.info(f"MT5 attached to existing session")
                logger.info(f"Account: {account_info.login}, Server: {account_info.server}")
                logger.info(f"Balance: {account_info.balance}, Equity: {account_info.equity}")
                
                return True
            
            # Otherwise, perform login with provided credentials
            authorized = mt5.login(
                login=self.login,
                password=self.password,
                server=self.server
            )
            
            if not authorized:
                error = mt5.last_error()
                logger.error(f"MT5 login failed: {error}")
                mt5.shutdown()
                return False
            
            # Verify connection after login
            account_info = mt5.account_info()
            if account_info is None:
                logger.error("Failed to get account info after login")
                mt5.shutdown()
                return False
            
            self.is_connected = True
            self.reconnect_attempts = 0
            
            logger.info(f"MT5 login successful (Account: {self.login})")
            logger.info(f"Account: {account_info.login}, Server: {account_info.server}")
            logger.info(f"Balance: {account_info.balance}, Equity: {account_info.equity}")
            
            return True
            
        except Exception as e:
            logger.error(f"MT5 connection error: {e}")
            self.is_connected = False
            return False
    
    def disconnect(self):
        """Disconnect from MT5."""
        if self.is_connected:
            mt5.shutdown()
            self.is_connected = False
            logger.info("MT5 disconnected")
    
    def reconnect(self) -> bool:
        """
        Reconnect to MT5.
        
        Returns:
            True if reconnected successfully
        """
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(f"Max reconnection attempts ({self.max_reconnect_attempts}) reached")
            return False
        
        self.reconnect_attempts += 1
        logger.warning(f"Reconnecting to MT5 (attempt {self.reconnect_attempts}/{self.max_reconnect_attempts})...")
        
        self.disconnect()
        time.sleep(self.reconnect_delay)
        
        return self.connect()
    
    def ensure_connected(self) -> bool:
        """
        Ensure MT5 is connected, reconnect if needed.
        
        Returns:
            True if connected
        """
        if not self.is_connected:
            return self.reconnect()
        return True
    
    def select_symbol(self, symbol: str) -> bool:
        """
        Select and enable symbol in Market Watch.
        
        Args:
            symbol: Symbol name (e.g., "EURUSD")
        
        Returns:
            True if symbol is available
        """
        if not self.ensure_connected():
            return False
        
        try:
            # Check if symbol exists
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                logger.error(f"Symbol {symbol} not found")
                return False
            
            # Add to Market Watch if not visible
            if not symbol_info.visible:
                if not mt5.symbol_select(symbol, True):
                    logger.error(f"Failed to add {symbol} to Market Watch")
                    return False
                logger.info(f"Added {symbol} to Market Watch")
            
            return True
            
        except Exception as e:
            logger.error(f"Error selecting symbol {symbol}: {e}")
            return False
    
    def get_tick(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get latest tick for symbol.
        
        Args:
            symbol: Symbol name
        
        Returns:
            Tick data dict or None if error
            Format: {"time": <unix_ms>, "bid": <float>, "ask": <float>}
        """
        if not self.ensure_connected():
            return None
        
        if not self.select_symbol(symbol):
            return None
        
        try:
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                logger.warning(f"No tick data for {symbol}")
                return None
            
            # Convert to dict with unix timestamp in milliseconds
            tick_time = int(tick.time * 1000) if hasattr(tick, 'time') else int(time.time() * 1000)
            
            # Get broker time as ISO format string
            if hasattr(tick, 'time'):
                broker_time_dt = datetime.fromtimestamp(tick.time)
                broker_time = broker_time_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                broker_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            
            # Get volume_real (MT5 tick volume represents real volume for that tick)
            volume_real = int(tick.volume) if hasattr(tick, 'volume') and tick.volume else 0
            
            return {
                "time": tick_time,
                "bid": float(tick.bid),
                "ask": float(tick.ask),
                "last": float(tick.last) if hasattr(tick, 'last') else None,
                "volume": int(tick.volume) if hasattr(tick, 'volume') else None,
                "volume_real": volume_real,
                "broker_time": broker_time
            }
            
        except Exception as e:
            logger.error(f"Error getting tick for {symbol}: {e}")
            return None
    
    def stream_ticks(self, symbol: str, callback):
        """
        Stream ticks for symbol (blocking).
        
        Args:
            symbol: Symbol name
            callback: Function to call with each tick (receives tick dict)
        
        Note: This is a blocking function. Use in a separate thread/task.
        """
        if not self.select_symbol(symbol):
            logger.error(f"Cannot stream ticks for {symbol}")
            return
        
        logger.info(f"Starting tick stream for {symbol}")
        last_tick_time = 0
        
        try:
            while True:
                if not self.ensure_connected():
                    logger.warning("Connection lost, attempting reconnect...")
                    time.sleep(self.reconnect_delay)
                    continue
                
                tick = self.get_tick(symbol)
                if tick and tick["time"] != last_tick_time:
                    last_tick_time = tick["time"]
                    callback(tick)
                
                # Small delay to avoid excessive polling
                time.sleep(0.01)  # 10ms = ~100 ticks/second max
                
        except KeyboardInterrupt:
            logger.info(f"Tick stream stopped for {symbol}")
        except Exception as e:
            logger.error(f"Error in tick stream for {symbol}: {e}")
    
    def get_bars(self, symbol: str, timeframe: int, count: int = 1000) -> Optional[List[Dict[str, Any]]]:
        """
        Get historical bars for symbol.
        
        Args:
            symbol: Symbol name
            timeframe: MT5 timeframe constant (e.g., mt5.TIMEFRAME_M1)
            count: Number of bars to retrieve
        
        Returns:
            List of bar dicts or None if error
            Format: [{"time": <unix_ms>, "open": <float>, "high": <float>, 
                     "low": <float>, "close": <float>, "volume": <float>}, ...]
        """
        if not self.ensure_connected():
            return None
        
        if not self.select_symbol(symbol):
            return None
        
        try:
            rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
            if rates is None:
                logger.warning(f"No bar data for {symbol}")
                return None
            
            bars = []
            for rate in rates:
                bars.append({
                    "time": int(rate.time * 1000),  # Convert to milliseconds
                    "open": float(rate.open),
                    "high": float(rate.high),
                    "low": float(rate.low),
                    "close": float(rate.close),
                    "volume": int(rate.tick_volume)
                })
            
            return bars
            
        except Exception as e:
            logger.error(f"Error getting bars for {symbol}: {e}")
            return None
    
    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()

