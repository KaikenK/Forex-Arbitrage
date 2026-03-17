"""
Broker Interfaces

Pluggable broker implementations for execution.
All brokers implement the BrokerInterface ABC.

Available Brokers:
- PaperBroker: Simulated execution for testing (default)
- MT5Broker: MetaTrader 5 execution stub
- RESTBroker: Generic REST API broker
"""

import time
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import random

logger = logging.getLogger(__name__)


class OrderSide(Enum):
    """Order side."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    """Order type."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class OrderStatus(Enum):
    """Order execution status."""
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


@dataclass
class OrderRequest:
    """
    Order request to be sent to a broker.
    
    Attributes:
        symbol: Currency pair symbol
        side: BUY or SELL
        quantity: Order quantity (lots)
        order_type: MARKET, LIMIT, or STOP
        price: Limit/stop price (optional for market orders)
        source_hint: Which data source triggered this order
        metadata: Additional order metadata
    """
    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    source_hint: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def __post_init__(self):
        if not self.request_id:
            self.request_id = f"ORD-{int(time.time() * 1000)}-{random.randint(1000, 9999)}"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "request_id": self.request_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "price": self.price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "source_hint": self.source_hint,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class OrderResult:
    """
    Result of an order execution attempt.
    
    Attributes:
        request_id: Original request ID
        order_id: Broker-assigned order ID
        status: Execution status
        filled_quantity: Quantity filled
        fill_price: Average fill price
        commission: Commission charged
        slippage_pips: Slippage from requested price
        execution_time_ms: Time to execute
        error_message: Error message if rejected
    """
    request_id: str
    order_id: str
    status: OrderStatus
    filled_quantity: float = 0.0
    fill_price: Optional[float] = None
    commission: float = 0.0
    slippage_pips: float = 0.0
    execution_time_ms: int = 0
    error_message: Optional[str] = None
    broker_response: Dict[str, Any] = field(default_factory=dict)
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "request_id": self.request_id,
            "order_id": self.order_id,
            "status": self.status.value,
            "filled_quantity": self.filled_quantity,
            "fill_price": self.fill_price,
            "commission": self.commission,
            "slippage_pips": round(self.slippage_pips, 2),
            "execution_time_ms": self.execution_time_ms,
            "error_message": self.error_message,
            "completed_at": self.completed_at.isoformat(),
        }


@dataclass
class Position:
    """
    Open position.
    """
    symbol: str
    side: OrderSide
    quantity: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    position_id: str
    opened_at: datetime
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "opened_at": self.opened_at.isoformat(),
        }


class BrokerInterface(ABC):
    """
    Abstract base class for broker implementations.
    
    All broker plugins must implement this interface.
    """
    
    def __init__(self, broker_id: str, display_name: str):
        self.broker_id = broker_id
        self.display_name = display_name
        self._connected = False
    
    @property
    def is_connected(self) -> bool:
        return self._connected
    
    @abstractmethod
    async def connect(self) -> bool:
        """Connect to the broker. Returns True if successful."""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the broker."""
        pass
    
    @abstractmethod
    async def submit_order(self, request: OrderRequest) -> OrderResult:
        """Submit an order. Returns execution result."""
        pass
    
    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order. Returns True if successful."""
        pass
    
    @abstractmethod
    async def get_positions(self) -> List[Position]:
        """Get all open positions."""
        pass
    
    @abstractmethod
    async def get_account_info(self) -> Dict[str, Any]:
        """Get account information (balance, equity, etc.)."""
        pass
    
    def get_info(self) -> Dict[str, Any]:
        """Get broker information."""
        return {
            "broker_id": self.broker_id,
            "display_name": self.display_name,
            "is_connected": self._connected,
        }


class PaperBroker(BrokerInterface):
    """
    Paper trading broker for testing and simulation.
    
    Simulates order execution with configurable latency,
    slippage, and fill rates.
    
    Usage:
        broker = PaperBroker(initial_balance=100000.0)
        await broker.connect()
        result = await broker.submit_order(order_request)
    """
    
    def __init__(self, initial_balance: float = 100000.0,
                 simulated_latency_ms: int = 50,
                 slippage_pips: float = 0.2,
                 fill_rate: float = 0.98):
        """
        Initialize paper broker.
        
        Args:
            initial_balance: Starting account balance
            simulated_latency_ms: Simulated execution latency
            slippage_pips: Simulated slippage
            fill_rate: Probability of order being filled
        """
        super().__init__("paper_broker", "Paper Trading Broker")
        
        self._initial_balance = initial_balance
        self._balance = initial_balance
        self._equity = initial_balance
        self._simulated_latency_ms = simulated_latency_ms
        self._slippage_pips = slippage_pips
        self._fill_rate = fill_rate
        
        self._positions: Dict[str, Position] = {}
        self._orders: Dict[str, OrderResult] = {}
        self._order_counter = 0
        
        logger.info(f"[PaperBroker] Initialized with balance={initial_balance}")
    
    async def connect(self) -> bool:
        """Connect to paper broker (always succeeds)."""
        self._connected = True
        logger.info("[PaperBroker] Connected")
        return True
    
    async def disconnect(self) -> None:
        """Disconnect from paper broker."""
        self._connected = False
        logger.info("[PaperBroker] Disconnected")
    
    async def submit_order(self, request: OrderRequest) -> OrderResult:
        """
        Submit a simulated order.
        
        Simulates execution with latency, slippage, and random fill rates.
        """
        start_time = time.time()
        
        # Simulate latency
        await asyncio.sleep(self._simulated_latency_ms / 1000)
        
        self._order_counter += 1
        order_id = f"PAPER-{self._order_counter}"
        
        # Check fill rate
        if random.random() > self._fill_rate:
            return OrderResult(
                request_id=request.request_id,
                order_id=order_id,
                status=OrderStatus.REJECTED,
                error_message="Simulated rejection (fill rate)",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )
        
        # Calculate fill price with slippage
        pip_value = 0.01 if "JPY" in request.symbol else 0.0001
        slippage = random.uniform(0, self._slippage_pips) * pip_value
        
        if request.price:
            base_price = request.price
        else:
            # For market orders without price, use a simulated price
            base_price = 1.0  # This would come from market data in real implementation
        
        if request.side == OrderSide.BUY:
            fill_price = base_price + slippage
        else:
            fill_price = base_price - slippage
        
        # Create position
        position_id = f"POS-{self._order_counter}"
        position = Position(
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            entry_price=fill_price,
            current_price=fill_price,
            unrealized_pnl=0.0,
            position_id=position_id,
            opened_at=datetime.now(timezone.utc),
        )
        self._positions[position_id] = position
        
        result = OrderResult(
            request_id=request.request_id,
            order_id=order_id,
            status=OrderStatus.FILLED,
            filled_quantity=request.quantity,
            fill_price=fill_price,
            commission=request.quantity * 3.0,  # $3 per lot
            slippage_pips=slippage / pip_value,
            execution_time_ms=int((time.time() - start_time) * 1000),
        )
        
        self._orders[order_id] = result
        
        logger.info(f"[PaperBroker] Order filled: {order_id} {request.side.value} {request.quantity} {request.symbol} @ {fill_price}")
        
        return result
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order (paper orders are instant, so nothing to cancel)."""
        logger.info(f"[PaperBroker] Cancel requested for {order_id}")
        return True
    
    async def get_positions(self) -> List[Position]:
        """Get all open positions."""
        return list(self._positions.values())
    
    async def get_account_info(self) -> Dict[str, Any]:
        """Get account information."""
        # Calculate unrealized P&L
        unrealized_pnl = sum(p.unrealized_pnl for p in self._positions.values())
        
        return {
            "broker_id": self.broker_id,
            "account_type": "paper",
            "balance": self._balance,
            "equity": self._balance + unrealized_pnl,
            "margin_used": len(self._positions) * 1000,  # $1000 per position
            "margin_available": self._balance - len(self._positions) * 1000,
            "open_positions": len(self._positions),
            "unrealized_pnl": round(unrealized_pnl, 2),
        }
    
    def close_position(self, position_id: str, close_price: float) -> Optional[float]:
        """
        Close a position and realize P&L.
        
        Returns realized P&L.
        """
        if position_id not in self._positions:
            return None
        
        position = self._positions.pop(position_id)
        pip_value = 0.01 if "JPY" in position.symbol else 0.0001
        
        if position.side == OrderSide.BUY:
            pnl_pips = (close_price - position.entry_price) / pip_value
        else:
            pnl_pips = (position.entry_price - close_price) / pip_value
        
        # Assume $10 per pip per lot
        pnl_dollars = pnl_pips * position.quantity * 10
        self._balance += pnl_dollars
        
        logger.info(f"[PaperBroker] Position closed: {position_id}, P&L: ${pnl_dollars:.2f}")
        
        return pnl_dollars


class MT5Broker(BrokerInterface):
    """
    MetaTrader 5 broker stub.
    
    Provides interface for MT5 execution. Actual implementation
    would use the MetaTrader5 Python library.
    
    Note: This is a stub. Full implementation requires MT5 terminal.
    """
    
    def __init__(self, path: Optional[str] = None, login: Optional[int] = None,
                 password: Optional[str] = None, server: Optional[str] = None):
        super().__init__("mt5_broker", "MetaTrader 5")
        
        self._path = path
        self._login = login
        self._password = password
        self._server = server
    
    async def connect(self) -> bool:
        """Connect to MT5."""
        try:
            # In real implementation:
            # import MetaTrader5 as mt5
            # if not mt5.initialize(path=self._path):
            #     return False
            # if self._login and not mt5.login(self._login, self._password, self._server):
            #     return False
            
            logger.warning("[MT5Broker] MT5 broker is a stub - not connected to real MT5")
            self._connected = False  # Set to True when actually connected
            return False
        except Exception as e:
            logger.error(f"[MT5Broker] Connection error: {e}")
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from MT5."""
        # mt5.shutdown()
        self._connected = False
        logger.info("[MT5Broker] Disconnected")
    
    async def submit_order(self, request: OrderRequest) -> OrderResult:
        """Submit order to MT5."""
        if not self._connected:
            return OrderResult(
                request_id=request.request_id,
                order_id="",
                status=OrderStatus.REJECTED,
                error_message="MT5 not connected",
            )
        
        # In real implementation:
        # request_dict = {
        #     "action": mt5.TRADE_ACTION_DEAL,
        #     "symbol": request.symbol,
        #     "volume": request.quantity,
        #     "type": mt5.ORDER_TYPE_BUY if request.side == OrderSide.BUY else mt5.ORDER_TYPE_SELL,
        #     "deviation": 20,
        #     "magic": 234000,
        #     "comment": "arbitrage",
        # }
        # result = mt5.order_send(request_dict)
        
        return OrderResult(
            request_id=request.request_id,
            order_id="",
            status=OrderStatus.REJECTED,
            error_message="MT5 broker is a stub",
        )
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel MT5 order."""
        return False
    
    async def get_positions(self) -> List[Position]:
        """Get MT5 positions."""
        return []
    
    async def get_account_info(self) -> Dict[str, Any]:
        """Get MT5 account info."""
        return {
            "broker_id": self.broker_id,
            "connected": self._connected,
            "error": "MT5 broker is a stub",
        }


class RESTBroker(BrokerInterface):
    """
    Generic REST API broker interface.
    
    Can be configured to work with various REST-based broker APIs.
    Requires endpoint configuration for each broker.
    """
    
    def __init__(self, base_url: str, api_key: str, api_secret: str,
                 broker_name: str = "REST Broker"):
        super().__init__("rest_broker", broker_name)
        
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._api_secret = api_secret
        self._session = None
    
    async def connect(self) -> bool:
        """Connect to REST broker."""
        try:
            import aiohttp
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                }
            )
            
            # Test connection
            async with self._session.get(f"{self._base_url}/account") as resp:
                if resp.status == 200:
                    self._connected = True
                    logger.info(f"[RESTBroker] Connected to {self._base_url}")
                    return True
                else:
                    logger.error(f"[RESTBroker] Connection failed: {resp.status}")
                    return False
        except Exception as e:
            logger.error(f"[RESTBroker] Connection error: {e}")
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from REST broker."""
        if self._session:
            await self._session.close()
            self._session = None
        self._connected = False
        logger.info("[RESTBroker] Disconnected")
    
    async def submit_order(self, request: OrderRequest) -> OrderResult:
        """Submit order via REST API."""
        if not self._connected or not self._session:
            return OrderResult(
                request_id=request.request_id,
                order_id="",
                status=OrderStatus.REJECTED,
                error_message="Not connected",
            )
        
        try:
            payload = {
                "symbol": request.symbol,
                "side": request.side.value.lower(),
                "quantity": request.quantity,
                "type": request.order_type.value.lower(),
            }
            
            if request.price:
                payload["price"] = request.price
            
            async with self._session.post(f"{self._base_url}/orders", json=payload) as resp:
                data = await resp.json()
                
                if resp.status in (200, 201):
                    return OrderResult(
                        request_id=request.request_id,
                        order_id=data.get("order_id", ""),
                        status=OrderStatus.FILLED,
                        filled_quantity=data.get("filled_quantity", request.quantity),
                        fill_price=data.get("fill_price"),
                        broker_response=data,
                    )
                else:
                    return OrderResult(
                        request_id=request.request_id,
                        order_id="",
                        status=OrderStatus.REJECTED,
                        error_message=data.get("error", f"HTTP {resp.status}"),
                        broker_response=data,
                    )
        except Exception as e:
            return OrderResult(
                request_id=request.request_id,
                order_id="",
                status=OrderStatus.REJECTED,
                error_message=str(e),
            )
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel order via REST API."""
        if not self._connected or not self._session:
            return False
        
        try:
            async with self._session.delete(f"{self._base_url}/orders/{order_id}") as resp:
                return resp.status in (200, 204)
        except Exception as e:
            logger.error(f"[RESTBroker] Cancel error: {e}")
            return False
    
    async def get_positions(self) -> List[Position]:
        """Get positions via REST API."""
        if not self._connected or not self._session:
            return []
        
        try:
            async with self._session.get(f"{self._base_url}/positions") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    positions = []
                    for p in data.get("positions", []):
                        positions.append(Position(
                            symbol=p["symbol"],
                            side=OrderSide.BUY if p["side"] == "buy" else OrderSide.SELL,
                            quantity=p["quantity"],
                            entry_price=p["entry_price"],
                            current_price=p.get("current_price", p["entry_price"]),
                            unrealized_pnl=p.get("unrealized_pnl", 0),
                            position_id=p["position_id"],
                            opened_at=datetime.fromisoformat(p["opened_at"]),
                        ))
                    return positions
                return []
        except Exception as e:
            logger.error(f"[RESTBroker] Get positions error: {e}")
            return []
    
    async def get_account_info(self) -> Dict[str, Any]:
        """Get account info via REST API."""
        if not self._connected or not self._session:
            return {"error": "Not connected"}
        
        try:
            async with self._session.get(f"{self._base_url}/account") as resp:
                if resp.status == 200:
                    return await resp.json()
                return {"error": f"HTTP {resp.status}"}
        except Exception as e:
            return {"error": str(e)}
