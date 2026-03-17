"""
TradingView WebSocket Message Parser

Parses TradingView's Socket.IO protocol messages and extracts tick data.
"""

import json
import re
import logging
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class TickData:
    """Structured tick data object."""
    
    def __init__(self, symbol: str, timestamp: int, bid: float = None, 
                 ask: float = None, last: float = None):
        self.symbol = symbol
        self.timestamp = timestamp
        self.bid = bid
        self.ask = ask
        self.last = last
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last
        }
    
    def __repr__(self):
        return f"TickData(symbol={self.symbol}, timestamp={self.timestamp}, bid={self.bid}, ask={self.ask}, last={self.last})"


class TradingViewParser:
    """Parser for TradingView WebSocket messages."""
    
    @staticmethod
    def parse_message(message: str) -> Optional[Dict]:
        """
        Parse TradingView Socket.IO message.
        
        TradingView uses Socket.IO protocol with format:
        ~m~<length>~m~<message>
        
        Returns parsed message or None if not a data message.
        """
        try:
            # Remove Socket.IO frame markers
            if message.startswith('~m~'):
                # Extract message length
                match = re.match(r'~m~(\d+)~m~(.*)', message)
                if match:
                    length = int(match.group(1))
                    payload = match.group(2)
                    
                    # Try to parse as JSON
                    try:
                        data = json.loads(payload)
                        return data
                    except json.JSONDecodeError:
                        # Sometimes it's a string message
                        return {"type": "message", "data": payload}
            
            # Handle other Socket.IO message types
            if message.startswith('~h~'):
                # Heartbeat
                return {"type": "heartbeat"}
            
            if message.startswith('~m~'):
                # Already handled above
                pass
            
            # Try direct JSON parse
            try:
                return json.loads(message)
            except:
                return None
                
        except Exception as e:
            logger.debug(f"Parse error: {e}, message: {message[:100]}")
            return None
    
    @staticmethod
    def extract_tick_data(parsed_message: Any, symbol: str) -> Optional[TickData]:
        """
        Extract tick data from parsed TradingView message.
        
        TradingView sends quote updates in format:
        ["q", session_id, {symbol: {bid, ask, lp, ...}}]
        
        Or:
        ["m", channel, [timestamp, bid, ask, last, ...]]
        """
        try:
            if not isinstance(parsed_message, list) or len(parsed_message) < 2:
                return None
            
            msg_type = parsed_message[0]
            
            # Quote update format: ["q", session_id, {symbol: data}]
            if msg_type == "q" and len(parsed_message) >= 3:
                quote_data = parsed_message[2]
                if isinstance(quote_data, dict):
                    # Extract data for our symbol
                    symbol_data = quote_data.get(symbol)
                    if symbol_data and isinstance(symbol_data, dict):
                        timestamp = int(datetime.now().timestamp() * 1000)
                        
                        # TradingView uses: "lp" (last price), "bid", "ask"
                        bid = symbol_data.get("bid") or symbol_data.get("b")
                        ask = symbol_data.get("ask") or symbol_data.get("a")
                        last = symbol_data.get("lp") or symbol_data.get("last") or symbol_data.get("l") or symbol_data.get("p")
                        
                        # Also check for timestamp
                        if "t" in symbol_data or "timestamp" in symbol_data:
                            ts = symbol_data.get("t") or symbol_data.get("timestamp")
                            if ts:
                                timestamp = int(ts)
                        
                        if bid is not None or ask is not None or last is not None:
                            return TickData(
                                symbol=symbol,
                                timestamp=timestamp,
                                bid=float(bid) if bid is not None else None,
                                ask=float(ask) if ask is not None else None,
                                last=float(last) if last is not None else None
                            )
            
            # Market data format: ["m", channel, [timestamp, bid, ask, last, volume, ...]]
            if msg_type == "m" and len(parsed_message) >= 3:
                data = parsed_message[2]
                if isinstance(data, list) and len(data) >= 4:
                    timestamp = int(data[0]) if isinstance(data[0], (int, float)) else int(datetime.now().timestamp() * 1000)
                    bid = float(data[1]) if len(data) > 1 and data[1] is not None else None
                    ask = float(data[2]) if len(data) > 2 and data[2] is not None else None
                    last = float(data[3]) if len(data) > 3 and data[3] is not None else None
                    
                    return TickData(
                        symbol=symbol,
                        timestamp=timestamp,
                        bid=bid,
                        ask=ask,
                        last=last
                    )
            
            # Direct dict format (fallback)
            if isinstance(parsed_message, dict):
                timestamp = parsed_message.get("timestamp") or parsed_message.get("t")
                if timestamp is None:
                    timestamp = int(datetime.now().timestamp() * 1000)
                else:
                    timestamp = int(timestamp)
                
                bid = parsed_message.get("bid") or parsed_message.get("b")
                ask = parsed_message.get("ask") or parsed_message.get("a")
                last = parsed_message.get("lp") or parsed_message.get("last") or parsed_message.get("l") or parsed_message.get("p")
                
                if bid is not None or ask is not None or last is not None:
                    return TickData(
                        symbol=symbol,
                        timestamp=timestamp,
                        bid=float(bid) if bid is not None else None,
                        ask=float(ask) if ask is not None else None,
                        last=float(last) if last is not None else None
                    )
            
            return None
            
        except Exception as e:
            logger.error(f"Error extracting tick data: {e}, message: {parsed_message}")
            return None
    
    @staticmethod
    def build_subscribe_message(session_id: str, symbol: str) -> str:
        """
        Build TradingView subscription message.
        
        TradingView uses format: ~m~<length>~m~<json_message>
        Message format: ["quote_add_symbols", session_id, [symbol]]
        Note: symbols must be in an array
        """
        message = ["quote_add_symbols", session_id, [symbol]]
        message_str = json.dumps(message)
        length = len(message_str)
        
        return f"~m~{length}~m~{message_str}"
    
    @staticmethod
    def build_heartbeat_message() -> str:
        """Build heartbeat message."""
        return "~h~"
    
    @staticmethod
    def build_set_auth_message(session_id: str) -> str:
        """Build authentication/session message."""
        # TradingView auth format: ["set_auth_token", "unauthorized_user_token"]
        # Note: session_id is not used in auth message, it's sent separately
        message = ["set_auth_token", "unauthorized_user_token"]
        message_str = json.dumps(message)
        length = len(message_str)
        
        return f"~m~{length}~m~{message_str}"
    
    @staticmethod
    def build_quote_hibernate_message(session_id: str, symbols: list) -> str:
        """Build quote hibernate message (unsubscribe)."""
        message = ["quote_hibernate_symbols", session_id] + symbols
        message_str = json.dumps(message)
        length = len(message_str)
        return f"~m~{length}~m~{message_str}"

