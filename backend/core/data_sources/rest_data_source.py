"""
REST API Data Source Plugin

Polls FX prices from REST API endpoints (e.g., free FX APIs, central bank rates).
Useful for INR/USD and other pairs not available via MT5.

Design Decisions:
- Async HTTP client for non-blocking operation
- Configurable polling interval
- Response parsing adapters for different API formats
- Rate limiting to respect API constraints
- Caching to reduce redundant requests
"""

import asyncio
import time
import logging
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field
from datetime import datetime
import threading

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

from backend.core.interfaces.data_source import (
    DataSourceInterface,
    DataSourceConfig,
    RawTick,
)

logger = logging.getLogger(__name__)


@dataclass
class RESTSourceConfig:
    """
    Configuration for REST API data source.
    
    Attributes:
        base_url: API base URL
        endpoint_template: URL template with {symbol} placeholder
        api_key: Optional API key for authentication
        headers: Custom headers for requests
        poll_interval_ms: Polling interval in milliseconds
        timeout_ms: Request timeout in milliseconds
        rate_limit_per_minute: Maximum requests per minute
        response_parser: Name of parser to use ("default", "exchangerate", "fixer", "custom")
        symbol_mapping: Map internal symbols to API symbols
    """
    base_url: str = ""
    endpoint_template: str = "/latest?base={base}&symbols={quote}"
    api_key: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    poll_interval_ms: int = 1000  # 1 second default
    timeout_ms: int = 5000
    rate_limit_per_minute: int = 60
    response_parser: str = "default"
    symbol_mapping: Dict[str, str] = field(default_factory=dict)
    
    # Parser-specific field mappings
    bid_field: str = "bid"
    ask_field: str = "ask"
    rate_field: str = "rate"  # For APIs that only provide mid rate
    timestamp_field: str = "timestamp"


class RESTDataSource(DataSourceInterface):
    """
    REST API-based FX data source.
    
    Polls external REST APIs for FX prices. Supports various API formats
    through configurable response parsers.
    
    Supported API formats:
    - exchangerate-api.com style
    - fixer.io style
    - Generic bid/ask JSON
    - Custom parser callback
    
    Example:
        config = DataSourceConfig(
            source_id="exchangerate_api",
            source_type="rest",
            display_name="ExchangeRate API",
            latency_estimate_ms=500.0,
            symbols=["USDINR", "EURINR"]
        )
        
        rest_config = RESTSourceConfig(
            base_url="https://api.exchangerate-api.com/v4",
            endpoint_template="/latest/{base}",
            poll_interval_ms=5000,
            response_parser="exchangerate",
        )
        
        source = RESTDataSource(config, rest_config)
        await source.connect_async()
    """
    
    def __init__(
        self,
        config: DataSourceConfig,
        rest_config: Optional[RESTSourceConfig] = None,
        custom_parser: Optional[Callable[[Dict, str], Optional[RawTick]]] = None,
    ):
        """
        Initialize REST data source.
        
        Args:
            config: Data source configuration
            rest_config: REST-specific configuration
            custom_parser: Optional custom response parser function
        """
        super().__init__(config)
        
        self._rest_config = rest_config or RESTSourceConfig()
        self._custom_parser = custom_parser
        
        # HTTP client
        self._client: Optional["httpx.AsyncClient"] = None
        
        # Cached prices
        self._cached_ticks: Dict[str, RawTick] = {}
        self._cache_times: Dict[str, int] = {}
        
        # Rate limiting
        self._request_times: List[float] = []
        self._lock = threading.Lock()
        
        # Polling state
        self._polling = False
        self._poll_task: Optional[asyncio.Task] = None
        
        # Stats
        self._requests_made = 0
        self._requests_failed = 0
    
    def connect(self) -> bool:
        """
        Synchronous connect (creates async client).
        
        Note: For full functionality, use connect_async() in async context.
        """
        if not HTTPX_AVAILABLE:
            logger.error("httpx not installed. Install with: pip install httpx")
            return False
        
        self._is_connected = True
        logger.info(f"[{self.source_id}] REST data source initialized")
        return True
    
    async def connect_async(self) -> bool:
        """
        Async connect - initializes HTTP client.
        """
        if not HTTPX_AVAILABLE:
            logger.error("httpx not installed. Install with: pip install httpx")
            return False
        
        headers = dict(self._rest_config.headers)
        if self._rest_config.api_key:
            headers["Authorization"] = f"Bearer {self._rest_config.api_key}"
        
        self._client = httpx.AsyncClient(
            base_url=self._rest_config.base_url,
            headers=headers,
            timeout=self._rest_config.timeout_ms / 1000.0,
        )
        
        self._is_connected = True
        logger.info(f"[{self.source_id}] REST data source connected to {self._rest_config.base_url}")
        return True
    
    def disconnect(self) -> None:
        """Disconnect and cleanup."""
        self._polling = False
        if self._poll_task:
            self._poll_task.cancel()
        self._is_connected = False
        logger.info(f"[{self.source_id}] REST data source disconnected")
    
    async def disconnect_async(self) -> None:
        """Async disconnect."""
        self._polling = False
        if self._poll_task:
            self._poll_task.cancel()
        if self._client:
            await self._client.aclose()
            self._client = None
        self._is_connected = False
    
    def get_tick(self, symbol: str) -> Optional[RawTick]:
        """
        Get cached tick for symbol.
        
        Note: Actual fetching happens in async poll loop.
        """
        return self._cached_ticks.get(symbol)
    
    async def fetch_tick(self, symbol: str) -> Optional[RawTick]:
        """
        Fetch fresh tick from API.
        
        Args:
            symbol: Symbol to fetch (e.g., "USDINR")
        
        Returns:
            RawTick if successful, None otherwise
        """
        if not self._client:
            await self.connect_async()
        
        # Check rate limit
        if not self._check_rate_limit():
            logger.warning(f"[{self.source_id}] Rate limit exceeded")
            return self._cached_ticks.get(symbol)
        
        try:
            # Parse symbol into base/quote
            base, quote = self._parse_symbol(symbol)
            
            # Map to API symbol if needed
            api_symbol = self._rest_config.symbol_mapping.get(symbol, symbol)
            
            # Build URL
            url = self._rest_config.endpoint_template.format(
                symbol=api_symbol,
                base=base,
                quote=quote,
            )
            
            # Make request
            response = await self._client.get(url)
            response.raise_for_status()
            
            self._requests_made += 1
            
            # Parse response
            data = response.json()
            tick = self._parse_response(data, symbol)
            
            if tick:
                self._cached_ticks[symbol] = tick
                self._cache_times[symbol] = int(time.time() * 1000)
                self._tick_count += 1
                self._last_tick_time = tick.timestamp_ms
            
            return tick
            
        except Exception as e:
            self._requests_failed += 1
            self._error_count += 1
            logger.error(f"[{self.source_id}] Error fetching {symbol}: {e}")
            return self._cached_ticks.get(symbol)
    
    def _parse_symbol(self, symbol: str) -> tuple:
        """Parse symbol into base and quote currencies."""
        if len(symbol) == 6:
            return symbol[:3], symbol[3:]
        elif "/" in symbol:
            parts = symbol.split("/")
            return parts[0], parts[1]
        else:
            return symbol, "USD"
    
    def _check_rate_limit(self) -> bool:
        """Check if we're within rate limits."""
        now = time.time()
        minute_ago = now - 60
        
        with self._lock:
            # Remove old request times
            self._request_times = [t for t in self._request_times if t > minute_ago]
            
            if len(self._request_times) >= self._rest_config.rate_limit_per_minute:
                return False
            
            self._request_times.append(now)
            return True
    
    def _parse_response(self, data: Dict, symbol: str) -> Optional[RawTick]:
        """
        Parse API response based on configured parser.
        """
        if self._custom_parser:
            return self._custom_parser(data, symbol)
        
        parser_name = self._rest_config.response_parser
        
        if parser_name == "exchangerate":
            return self._parse_exchangerate(data, symbol)
        elif parser_name == "fixer":
            return self._parse_fixer(data, symbol)
        else:
            return self._parse_default(data, symbol)
    
    def _parse_default(self, data: Dict, symbol: str) -> Optional[RawTick]:
        """Default parser for bid/ask JSON."""
        try:
            bid = float(data.get(self._rest_config.bid_field, 0))
            ask = float(data.get(self._rest_config.ask_field, 0))
            
            # If only rate provided, create synthetic bid/ask
            if bid == 0 and ask == 0:
                rate = float(data.get(self._rest_config.rate_field, 0))
                if rate > 0:
                    spread = rate * 0.0001  # 1 pip spread
                    bid = rate - spread / 2
                    ask = rate + spread / 2
            
            if bid == 0 or ask == 0:
                return None
            
            timestamp = data.get(self._rest_config.timestamp_field)
            if isinstance(timestamp, (int, float)):
                timestamp_ms = int(timestamp * 1000) if timestamp < 10000000000 else int(timestamp)
            else:
                timestamp_ms = int(time.time() * 1000)
            
            return RawTick(
                symbol=symbol,
                bid=bid,
                ask=ask,
                timestamp_ms=timestamp_ms,
                source_id=self.source_id,
            )
        except Exception as e:
            logger.error(f"[{self.source_id}] Parse error: {e}")
            return None
    
    def _parse_exchangerate(self, data: Dict, symbol: str) -> Optional[RawTick]:
        """Parser for exchangerate-api.com format."""
        try:
            rates = data.get("rates", {})
            base, quote = self._parse_symbol(symbol)
            
            if quote in rates:
                rate = float(rates[quote])
            elif base in rates:
                rate = 1.0 / float(rates[base])
            else:
                return None
            
            # Synthetic bid/ask (these APIs don't provide spreads)
            spread = rate * 0.0002  # 2 pip equivalent
            bid = rate - spread / 2
            ask = rate + spread / 2
            
            return RawTick(
                symbol=symbol,
                bid=bid,
                ask=ask,
                timestamp_ms=int(time.time() * 1000),
                source_id=self.source_id,
            )
        except Exception as e:
            logger.error(f"[{self.source_id}] ExchangeRate parse error: {e}")
            return None
    
    def _parse_fixer(self, data: Dict, symbol: str) -> Optional[RawTick]:
        """Parser for fixer.io format."""
        try:
            rates = data.get("rates", {})
            base, quote = self._parse_symbol(symbol)
            
            if quote in rates:
                rate = float(rates[quote])
                spread = rate * 0.0002
                bid = rate - spread / 2
                ask = rate + spread / 2
                
                return RawTick(
                    symbol=symbol,
                    bid=bid,
                    ask=ask,
                    timestamp_ms=int(data.get("timestamp", time.time()) * 1000),
                    source_id=self.source_id,
                )
            return None
        except Exception as e:
            logger.error(f"[{self.source_id}] Fixer parse error: {e}")
            return None
    
    async def start_polling(self, symbols: List[str]) -> None:
        """
        Start polling loop for symbols.
        
        Args:
            symbols: List of symbols to poll
        """
        self._polling = True
        
        while self._polling:
            for symbol in symbols:
                if symbol in self._config.symbols or not self._config.symbols:
                    await self.fetch_tick(symbol)
            
            await asyncio.sleep(self._rest_config.poll_interval_ms / 1000.0)
    
    def get_supported_symbols(self) -> List[str]:
        """Get list of supported symbols."""
        return self._config.symbols
    
    def is_healthy(self) -> bool:
        """Check if source is healthy."""
        if not self._is_connected:
            return False
        
        # Check if we've had successful requests recently
        if self._requests_made > 0:
            error_rate = self._requests_failed / self._requests_made
            return error_rate < 0.5
        
        return True
    
    def get_stats(self) -> Dict[str, Any]:
        """Get source statistics."""
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "is_connected": self._is_connected,
            "tick_count": self._tick_count,
            "error_count": self._error_count,
            "last_tick_time": self._last_tick_time,
            "latency_estimate_ms": self._config.latency_estimate_ms,
            "reliability_score": self._config.reliability_score,
            "requests_made": self._requests_made,
            "requests_failed": self._requests_failed,
            "cached_symbols": list(self._cached_ticks.keys()),
            "poll_interval_ms": self._rest_config.poll_interval_ms,
        }
