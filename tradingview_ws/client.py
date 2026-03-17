"""
TradingView WebSocket Client

Production-ready WebSocket client for streaming live FX tick data from TradingView.
"""

import websocket
import json
import time
import threading
import logging
import uuid
from typing import Optional, Callable, List, Dict, Any
from datetime import datetime

from .parser import TradingViewParser, TickData

logger = logging.getLogger(__name__)


class TradingViewWS:
    """
    TradingView WebSocket Client for real-time FX tick data.
    
    Features:
    - Auto-reconnection on disconnect
    - Heartbeat pings every 15 seconds
    - Multiple symbol support
    - CSV logging
    - Graceful shutdown
    
    Usage:
        def on_tick(tick):
            print(tick.to_dict())
        
        client = TradingViewWS(
            symbol="FX_IDC:EURUSD",
            on_tick=on_tick
        )
        client.start()
    """
    
    # ============================================================================
    # SETTINGS - CONFIGURE HERE
    # ============================================================================
    
    # WebSocket Configuration
    WS_URL = "wss://data.tradingview.com/socket.io/websocket"
    HEARTBEAT_INTERVAL = 15  # seconds
    RECONNECT_DELAY = 5  # seconds
    MAX_RECONNECT_ATTEMPTS = 10
    
    # Default symbol
    DEFAULT_SYMBOL = "FX_IDC:EURUSD"
    
    # Test mode (print only 1 tick per second)
    TEST_MODE = False
    
    # CSV Logging
    ENABLE_CSV_LOG = True
    CSV_LOG_DIR = "."
    
    # Debug Mode (shows raw messages)
    DEBUG_MODE = False
    
    # ============================================================================
    
    def __init__(self, symbol: str = None, symbols: List[str] = None, 
                 on_tick: Callable[[TickData], None] = None,
                 test_mode: bool = None,
                 debug: bool = None):
        """
        Initialize TradingView WebSocket client.
        
        Args:
            symbol: Single symbol to subscribe (e.g., "FX_IDC:EURUSD")
            symbols: List of symbols to subscribe
            on_tick: Callback function called for each tick (receives TickData)
            test_mode: Override default TEST_MODE setting
        """
        self.symbols = []
        if symbols:
            self.symbols = symbols
        elif symbol:
            self.symbols = [symbol]
        else:
            self.symbols = [self.DEFAULT_SYMBOL]
        
        self.on_tick = on_tick or self._default_tick_handler
        self.test_mode = test_mode if test_mode is not None else self.TEST_MODE
        self.DEBUG_MODE = debug if debug is not None else self.DEBUG_MODE
        
        # Connection state
        self.ws: Optional[websocket.WebSocketApp] = None
        self.session_id: str = ""
        self.is_connected = False
        self.is_running = False
        self.reconnect_count = 0
        
        # Threading
        self.heartbeat_thread: Optional[threading.Thread] = None
        self.ws_thread: Optional[threading.Thread] = None
        
        # CSV logging
        self.csv_files = {}
        if self.ENABLE_CSV_LOG:
            self._init_csv_logs()
        
        # Rate limiting for test mode
        self.last_tick_time = {}
        
        # Parser
        self.parser = TradingViewParser()
        
        logger.info(f"Initialized TradingViewWS client for symbols: {self.symbols}")
    
    def _init_csv_logs(self):
        """Initialize CSV log files for each symbol."""
        import csv
        import os
        
        for symbol in self.symbols:
            # Clean symbol name for filename
            clean_symbol = symbol.replace(":", "_").replace("/", "_")
            filename = os.path.join(self.CSV_LOG_DIR, f"ticks_{clean_symbol}.csv")
            
            # Create file with headers if it doesn't exist
            if not os.path.exists(filename):
                with open(filename, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(['timestamp', 'symbol', 'bid', 'ask', 'last'])
            
            self.csv_files[symbol] = filename
            logger.info(f"CSV log initialized: {filename}")
    
    def _log_to_csv(self, tick: TickData):
        """Log tick to CSV file."""
        if not self.ENABLE_CSV_LOG or tick.symbol not in self.csv_files:
            return
        
        try:
            import csv
            filename = self.csv_files[tick.symbol]
            with open(filename, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    tick.timestamp,
                    tick.symbol,
                    tick.bid if tick.bid is not None else '',
                    tick.ask if tick.ask is not None else '',
                    tick.last if tick.last is not None else ''
                ])
        except Exception as e:
            logger.error(f"CSV logging error: {e}")
    
    def _default_tick_handler(self, tick: TickData):
        """Default tick handler - prints tick data."""
        print(f"[{datetime.fromtimestamp(tick.timestamp/1000).strftime('%H:%M:%S.%f')[:-3]}] "
              f"{tick.symbol}: bid={tick.bid}, ask={tick.ask}, last={tick.last}")
    
    def _generate_session_id(self) -> str:
        """Generate TradingView session ID."""
        # TradingView uses a specific format, but we can use UUID
        return str(uuid.uuid4()).replace('-', '')[:16]
    
    def _on_message(self, ws, message):
        """Handle incoming WebSocket messages."""
        try:
            # Log raw message for debugging
            if not hasattr(self, '_msg_count'):
                self._msg_count = 0
            self._msg_count += 1
            
            # Debug logging for first messages or if debug mode enabled
            if self.DEBUG_MODE or self._msg_count <= 10:
                logger.info(f"[DEBUG] Message {self._msg_count}: {message[:300]}")
            
            # Handle Socket.IO protocol messages
            if message == "~h~" or message.startswith("~h~"):
                # Heartbeat response
                logger.debug("Heartbeat received")
                return
            
            # Parse message
            parsed = self.parser.parse_message(message)
            
            if not parsed:
                return
            
            # Handle session establishment
            # TradingView sends session ID in various formats
            if isinstance(parsed, dict):
                # Check for session ID in different possible fields
                session_id = parsed.get("session_id") or parsed.get("sid") or parsed.get("session")
                if session_id:
                    self.session_id = session_id
                    logger.info(f"Session established: {self.session_id}")
                    # Wait a bit before subscribing
                    time.sleep(1.0)
                    self._subscribe_to_symbols()
                    return
                
                # Check for heartbeat
                if parsed.get("type") == "heartbeat":
                    return
            
            # Handle array messages (TradingView protocol)
            if isinstance(parsed, list) and len(parsed) > 0:
                msg_type = parsed[0]
                
                # Session confirmation: ["~", "session_id"]
                if msg_type == "~" and len(parsed) > 1:
                    self.session_id = parsed[1]
                    logger.info(f"Session ID from array: {self.session_id}")
                    time.sleep(1.0)
                    self._subscribe_to_symbols()
                    return
            
            # Check for auth confirmation or other protocol messages
            if isinstance(parsed, list) and len(parsed) > 0:
                msg_type = parsed[0]
                
                # Auth confirmation or other protocol messages
                if msg_type in ["set_auth_token", "quote_add_symbols"]:
                    if self.DEBUG_MODE:
                        logger.info(f"[DEBUG] Protocol response: {parsed}")
                    # These are responses to our messages, continue processing
                    pass
            
            # Extract tick data for each symbol
            for symbol in self.symbols:
                tick = self.parser.extract_tick_data(parsed, symbol)
                
                if tick:
                    # Test mode: limit to 1 tick per second per symbol
                    if self.test_mode:
                        now = time.time()
                        if symbol in self.last_tick_time:
                            if now - self.last_tick_time[symbol] < 1.0:
                                continue
                        self.last_tick_time[symbol] = now
                    
                    # Log to CSV
                    self._log_to_csv(tick)
                    
                    # Call callback
                    try:
                        self.on_tick(tick)
                    except Exception as e:
                        logger.error(f"Error in tick callback: {e}")
                        
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    def _on_error(self, ws, error):
        """Handle WebSocket errors."""
        logger.error(f"WebSocket error: {error}")
    
    def _on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket close."""
        self.is_connected = False
        logger.warning(f"WebSocket closed: status={close_status_code}, msg={close_msg}")
        
        # Auto-reconnect if still running
        if self.is_running:
            self._reconnect()
    
    def _on_open(self, ws):
        """Handle WebSocket open."""
        self.is_connected = True
        self.reconnect_count = 0
        self._msg_count = 0
        logger.info("WebSocket connection opened")
        
        # Don't send anything immediately - wait for TradingView's initial message
        # TradingView will send session info in the first message(s)
        # We'll extract session ID and then subscribe
    
    def _subscribe_to_symbols(self):
        """Subscribe to all configured symbols."""
        if not self.ws or not self.is_connected or not self.session_id:
            logger.warning("Cannot subscribe: missing connection or session ID")
            return
        
        # Send auth first (required before subscription)
        try:
            auth_msg = self.parser.build_set_auth_message(self.session_id)
            if self.DEBUG_MODE:
                logger.info(f"[DEBUG] Sending auth: {auth_msg}")
            self.ws.send(auth_msg)
            logger.debug("Auth message sent")
            time.sleep(1.0)  # Wait for auth confirmation
        except Exception as e:
            logger.error(f"Error sending auth: {e}")
            return
        
        # Subscribe to symbols
        for symbol in self.symbols:
            try:
                # Build subscription message
                subscribe_msg = self.parser.build_subscribe_message(self.session_id, symbol)
                if self.DEBUG_MODE:
                    logger.info(f"[DEBUG] Sending subscribe: {subscribe_msg}")
                self.ws.send(subscribe_msg)
                logger.info(f"Subscribed to {symbol}")
                time.sleep(0.5)  # Delay between subscriptions
            except Exception as e:
                logger.error(f"Error subscribing to {symbol}: {e}")
    
    def _start_heartbeat(self):
        """Start heartbeat thread."""
        if self.heartbeat_thread and self.heartbeat_thread.is_alive():
            return
        
        def heartbeat_loop():
            while self.is_running and self.is_connected:
                try:
                    if self.ws:
                        heartbeat = self.parser.build_heartbeat_message()
                        self.ws.send(heartbeat)
                        logger.debug("Heartbeat sent")
                    time.sleep(self.HEARTBEAT_INTERVAL)
                except Exception as e:
                    logger.error(f"Heartbeat error: {e}")
                    break
        
        self.heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()
    
    def _reconnect(self):
        """Reconnect to WebSocket."""
        if self.reconnect_count >= self.MAX_RECONNECT_ATTEMPTS:
            logger.error(f"Max reconnection attempts ({self.MAX_RECONNECT_ATTEMPTS}) reached. Stopping.")
            self.stop()
            return
        
        self.reconnect_count += 1
        logger.info(f"Reconnecting in {self.RECONNECT_DELAY} seconds (attempt {self.reconnect_count}/{self.MAX_RECONNECT_ATTEMPTS})...")
        
        time.sleep(self.RECONNECT_DELAY)
        
        if self.is_running:
            self._connect()
    
    def _connect(self):
        """Establish WebSocket connection."""
        try:
            logger.info(f"Connecting to {self.WS_URL}...")
            
            self.ws = websocket.WebSocketApp(
                self.WS_URL,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
                on_open=self._on_open
            )
            
            # Run in separate thread
            def run_ws():
                self.ws.run_forever()
            
            self.ws_thread = threading.Thread(target=run_ws, daemon=True)
            self.ws_thread.start()
            
        except Exception as e:
            logger.error(f"Connection error: {e}")
            if self.is_running:
                self._reconnect()
    
    def start(self):
        """Start the WebSocket client."""
        if self.is_running:
            logger.warning("Client is already running")
            return
        
        self.is_running = True
        logger.info("Starting TradingView WebSocket client...")
        logger.info(f"Symbols: {self.symbols}")
        logger.info(f"Test mode: {self.test_mode}")
        logger.info("-" * 60)
        
        self._connect()
    
    def stop(self):
        """Stop the WebSocket client."""
        logger.info("Stopping TradingView WebSocket client...")
        self.is_running = False
        self.is_connected = False
        
        if self.ws:
            try:
                self.ws.close()
            except:
                pass
        
        logger.info("Client stopped")
    
    def wait(self):
        """Wait for client to finish (blocks until stopped)."""
        try:
            while self.is_running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
            self.stop()

