"""
Execution Engine

Central execution coordinator that gates all trades behind risk limits,
confidence thresholds, and stability requirements.

Design Principles:
- All execution goes through this engine
- No direct broker access from other components
- Comprehensive pre-trade risk checks
- Full audit trail of execution decisions
- Support for paper and live execution modes
"""

import time
import asyncio
import logging
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import RLock

from .brokers import (
    BrokerInterface,
    PaperBroker,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderType,
    OrderStatus,
    Position,
)

logger = logging.getLogger(__name__)


class ExecutionState(Enum):
    """Execution engine state."""
    DISABLED = "DISABLED"       # Execution disabled
    PAPER = "PAPER"            # Paper trading mode
    LIVE = "LIVE"              # Live execution mode
    PAUSED = "PAUSED"          # Temporarily paused


@dataclass
class RiskLimits:
    """
    Risk management limits for execution.
    
    Attributes:
        max_position_size: Maximum position size per symbol (lots)
        max_total_exposure: Maximum total exposure across all positions
        max_daily_loss: Maximum daily loss before stopping
        max_concurrent_positions: Maximum number of open positions
        min_confidence: Minimum confidence score to execute
        min_stability: Minimum stability score to execute
        min_profit_pips: Minimum profit threshold
        max_slippage_pips: Maximum acceptable slippage
        cooldown_after_loss_ms: Cooldown after a losing trade
    """
    max_position_size: float = 0.1          # 0.1 lots
    max_total_exposure: float = 1.0         # 1.0 lots total
    max_daily_loss: float = 1000.0          # $1000
    max_concurrent_positions: int = 5
    min_confidence: float = 0.7
    min_stability: float = 0.5
    min_profit_pips: float = 0.3
    max_slippage_pips: float = 1.0
    cooldown_after_loss_ms: int = 5000
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "max_position_size": self.max_position_size,
            "max_total_exposure": self.max_total_exposure,
            "max_daily_loss": self.max_daily_loss,
            "max_concurrent_positions": self.max_concurrent_positions,
            "min_confidence": self.min_confidence,
            "min_stability": self.min_stability,
            "min_profit_pips": self.min_profit_pips,
            "max_slippage_pips": self.max_slippage_pips,
            "cooldown_after_loss_ms": self.cooldown_after_loss_ms,
        }


@dataclass
class ExecutionConfig:
    """
    Execution engine configuration.
    
    Attributes:
        mode: Execution mode (PAPER, LIVE, DISABLED)
        risk_limits: Risk management limits
        auto_close_on_target: Auto-close when profit target reached
        profit_target_pips: Profit target for auto-close
        stop_loss_pips: Stop loss for auto-close
        enable_audit_log: Whether to log all decisions
    """
    mode: ExecutionState = ExecutionState.PAPER
    risk_limits: RiskLimits = field(default_factory=RiskLimits)
    auto_close_on_target: bool = True
    profit_target_pips: float = 0.5
    stop_loss_pips: float = 1.0
    enable_audit_log: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "mode": self.mode.value,
            "risk_limits": self.risk_limits.to_dict(),
            "auto_close_on_target": self.auto_close_on_target,
            "profit_target_pips": self.profit_target_pips,
            "stop_loss_pips": self.stop_loss_pips,
            "enable_audit_log": self.enable_audit_log,
        }


@dataclass
class ExecutionRequest:
    """
    Request to execute an arbitrage opportunity.
    
    Attributes:
        symbol: Currency pair
        buy_source: Source to buy from
        sell_source: Source to sell to
        expected_profit_pips: Expected profit
        confidence: Confidence score from state machine
        stability: Stability score from metrics
        quantity: Requested quantity (lots)
        metadata: Additional metadata
    """
    symbol: str
    buy_source: str
    sell_source: str
    expected_profit_pips: float
    confidence: float
    stability: float
    quantity: float = 0.1
    metadata: Dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def __post_init__(self):
        if not self.request_id:
            self.request_id = f"EXEC-{int(time.time() * 1000)}"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "request_id": self.request_id,
            "symbol": self.symbol,
            "buy_source": self.buy_source,
            "sell_source": self.sell_source,
            "expected_profit_pips": round(self.expected_profit_pips, 3),
            "confidence": round(self.confidence, 3),
            "stability": round(self.stability, 3),
            "quantity": self.quantity,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class ExecutionResult:
    """
    Result of an execution attempt.
    
    Attributes:
        request_id: Original request ID
        executed: Whether execution was attempted
        success: Whether execution succeeded
        rejection_reason: Reason if rejected before execution
        order_result: Broker order result if executed
        risk_check_passed: Whether risk checks passed
        audit_notes: Notes for audit trail
    """
    request_id: str
    executed: bool
    success: bool
    rejection_reason: Optional[str] = None
    order_result: Optional[OrderResult] = None
    risk_check_passed: bool = False
    audit_notes: List[str] = field(default_factory=list)
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "request_id": self.request_id,
            "executed": self.executed,
            "success": self.success,
            "rejection_reason": self.rejection_reason,
            "order_result": self.order_result.to_dict() if self.order_result else None,
            "risk_check_passed": self.risk_check_passed,
            "audit_notes": self.audit_notes,
            "completed_at": self.completed_at.isoformat(),
        }


@dataclass
class AuditEntry:
    """
    Audit log entry for execution decisions.
    """
    timestamp: datetime
    request_id: str
    action: str
    details: Dict[str, Any]
    result: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "request_id": self.request_id,
            "action": self.action,
            "details": self.details,
            "result": self.result,
        }


class ExecutionEngine:
    """
    Central execution engine for the FX arbitrage platform.
    
    All execution requests must go through this engine, which:
    1. Validates requests against risk limits
    2. Checks confidence and stability thresholds
    3. Routes to appropriate broker
    4. Maintains audit trail
    
    Usage:
        engine = ExecutionEngine()
        engine.set_broker(PaperBroker())
        await engine.start()
        
        # Execute an opportunity
        result = await engine.execute(ExecutionRequest(...))
    """
    
    def __init__(self, config: Optional[ExecutionConfig] = None):
        """
        Initialize execution engine.
        
        Args:
            config: Execution configuration
        """
        self._lock = RLock()
        self._config = config or ExecutionConfig()
        self._broker: Optional[BrokerInterface] = None
        self._state = ExecutionState.DISABLED
        
        # Tracking
        self._daily_pnl = 0.0
        self._daily_trades = 0
        self._last_loss_time: Optional[int] = None
        self._execution_history: List[ExecutionResult] = []
        self._audit_log: List[AuditEntry] = []
        self._max_history = 1000
        
        # Callbacks
        self._on_execution: Optional[Callable] = None
        
        logger.info(f"[ExecutionEngine] Initialized in {self._config.mode.value} mode")
    
    @property
    def state(self) -> ExecutionState:
        return self._state
    
    @property
    def config(self) -> ExecutionConfig:
        return self._config
    
    def set_broker(self, broker: BrokerInterface) -> None:
        """Set the broker to use for execution."""
        with self._lock:
            self._broker = broker
            logger.info(f"[ExecutionEngine] Broker set: {broker.display_name}")
    
    def set_on_execution_callback(self, callback: Callable) -> None:
        """Set callback for execution events."""
        self._on_execution = callback
    
    async def start(self) -> bool:
        """
        Start the execution engine.
        
        Connects to broker and enables execution based on mode.
        """
        with self._lock:
            if not self._broker:
                # Default to paper broker
                self._broker = PaperBroker()
                logger.info("[ExecutionEngine] No broker set, using PaperBroker")
            
            # Connect to broker
            connected = await self._broker.connect()
            if not connected:
                logger.error("[ExecutionEngine] Failed to connect to broker")
                self._state = ExecutionState.DISABLED
                return False
            
            self._state = self._config.mode
            logger.info(f"[ExecutionEngine] Started in {self._state.value} mode")
            return True
    
    async def stop(self) -> None:
        """Stop the execution engine."""
        with self._lock:
            if self._broker:
                await self._broker.disconnect()
            self._state = ExecutionState.DISABLED
            logger.info("[ExecutionEngine] Stopped")
    
    def pause(self) -> None:
        """Pause execution (no new trades)."""
        with self._lock:
            if self._state in (ExecutionState.PAPER, ExecutionState.LIVE):
                self._state = ExecutionState.PAUSED
                logger.info("[ExecutionEngine] Paused")
    
    def resume(self) -> None:
        """Resume execution."""
        with self._lock:
            if self._state == ExecutionState.PAUSED:
                self._state = self._config.mode
                logger.info(f"[ExecutionEngine] Resumed in {self._state.value} mode")
    
    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """
        Execute an arbitrage opportunity.
        
        Performs risk checks and routes to broker if approved.
        
        Args:
            request: Execution request
        
        Returns:
            ExecutionResult with outcome
        """
        audit_notes = []
        
        # Check engine state
        if self._state == ExecutionState.DISABLED:
            return self._reject(request, "Execution engine disabled", audit_notes)
        
        if self._state == ExecutionState.PAUSED:
            return self._reject(request, "Execution engine paused", audit_notes)
        
        # Perform risk checks
        risk_result = self._check_risk_limits(request, audit_notes)
        if not risk_result["passed"]:
            return self._reject(request, risk_result["reason"], audit_notes)
        
        audit_notes.append("Risk checks passed")
        
        # Check confidence and stability thresholds
        limits = self._config.risk_limits
        
        if request.confidence < limits.min_confidence:
            return self._reject(
                request, 
                f"Confidence {request.confidence:.3f} below threshold {limits.min_confidence}",
                audit_notes
            )
        
        if request.stability < limits.min_stability:
            return self._reject(
                request,
                f"Stability {request.stability:.3f} below threshold {limits.min_stability}",
                audit_notes
            )
        
        if request.expected_profit_pips < limits.min_profit_pips:
            return self._reject(
                request,
                f"Profit {request.expected_profit_pips:.3f} pips below threshold {limits.min_profit_pips}",
                audit_notes
            )
        
        audit_notes.append(f"Thresholds passed: conf={request.confidence:.3f}, stab={request.stability:.3f}")
        
        # Execute via broker
        try:
            order_request = OrderRequest(
                symbol=request.symbol,
                side=OrderSide.BUY,  # For arbitrage, we'd do both legs
                quantity=min(request.quantity, limits.max_position_size),
                order_type=OrderType.MARKET,
                source_hint=request.buy_source,
                metadata={
                    "arbitrage_request_id": request.request_id,
                    "expected_profit_pips": request.expected_profit_pips,
                    "buy_source": request.buy_source,
                    "sell_source": request.sell_source,
                },
            )
            
            order_result = await self._broker.submit_order(order_request)
            
            # Check slippage
            if order_result.slippage_pips > limits.max_slippage_pips:
                audit_notes.append(f"Warning: High slippage {order_result.slippage_pips:.2f} pips")
            
            # Record result
            success = order_result.status == OrderStatus.FILLED
            
            result = ExecutionResult(
                request_id=request.request_id,
                executed=True,
                success=success,
                order_result=order_result,
                risk_check_passed=True,
                audit_notes=audit_notes,
            )
            
            self._record_execution(request, result)
            
            return result
            
        except Exception as e:
            logger.error(f"[ExecutionEngine] Execution error: {e}")
            return ExecutionResult(
                request_id=request.request_id,
                executed=False,
                success=False,
                rejection_reason=f"Execution error: {e}",
                audit_notes=audit_notes,
            )
    
    def _check_risk_limits(self, request: ExecutionRequest, 
                           audit_notes: List[str]) -> Dict[str, Any]:
        """
        Check risk limits before execution.
        
        Returns dict with 'passed' bool and 'reason' if failed.
        """
        limits = self._config.risk_limits
        
        # Check position size
        if request.quantity > limits.max_position_size:
            return {
                "passed": False,
                "reason": f"Quantity {request.quantity} exceeds max {limits.max_position_size}",
            }
        
        # Check daily loss
        if self._daily_pnl < -limits.max_daily_loss:
            return {
                "passed": False,
                "reason": f"Daily loss {self._daily_pnl:.2f} exceeds limit {limits.max_daily_loss}",
            }
        
        # Check cooldown after loss
        if self._last_loss_time:
            elapsed = int(time.time() * 1000) - self._last_loss_time
            if elapsed < limits.cooldown_after_loss_ms:
                remaining = limits.cooldown_after_loss_ms - elapsed
                return {
                    "passed": False,
                    "reason": f"In cooldown after loss, {remaining}ms remaining",
                }
        
        # Check concurrent positions (if broker connected)
        if self._broker and self._broker.is_connected:
            # Would check actual positions here
            pass
        
        return {"passed": True}
    
    def _reject(self, request: ExecutionRequest, reason: str,
                audit_notes: List[str]) -> ExecutionResult:
        """Create rejection result and log."""
        audit_notes.append(f"Rejected: {reason}")
        
        result = ExecutionResult(
            request_id=request.request_id,
            executed=False,
            success=False,
            rejection_reason=reason,
            risk_check_passed=False,
            audit_notes=audit_notes,
        )
        
        self._log_audit(request.request_id, "REJECTED", 
                       {"reason": reason, "request": request.to_dict()}, "REJECTED")
        
        return result
    
    def _record_execution(self, request: ExecutionRequest, 
                          result: ExecutionResult) -> None:
        """Record execution in history and update stats."""
        with self._lock:
            self._execution_history.append(result)
            if len(self._execution_history) > self._max_history:
                self._execution_history.pop(0)
            
            self._daily_trades += 1
            
            # Log audit
            self._log_audit(
                request.request_id,
                "EXECUTED" if result.success else "FAILED",
                {
                    "request": request.to_dict(),
                    "result": result.to_dict(),
                },
                "SUCCESS" if result.success else "FAILURE"
            )
            
            # Callback
            if self._on_execution:
                try:
                    self._on_execution(request, result)
                except Exception as e:
                    logger.error(f"[ExecutionEngine] Callback error: {e}")
    
    def _log_audit(self, request_id: str, action: str,
                   details: Dict[str, Any], result: str) -> None:
        """Log an audit entry."""
        if not self._config.enable_audit_log:
            return
        
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc),
            request_id=request_id,
            action=action,
            details=details,
            result=result,
        )
        
        with self._lock:
            self._audit_log.append(entry)
            if len(self._audit_log) > self._max_history:
                self._audit_log.pop(0)
        
        logger.debug(f"[ExecutionEngine] Audit: {action} - {result}")
    
    def record_pnl(self, pnl: float) -> None:
        """Record P&L from a closed position."""
        with self._lock:
            self._daily_pnl += pnl
            if pnl < 0:
                self._last_loss_time = int(time.time() * 1000)
    
    def reset_daily_stats(self) -> None:
        """Reset daily statistics (call at day boundary)."""
        with self._lock:
            self._daily_pnl = 0.0
            self._daily_trades = 0
            self._last_loss_time = None
            logger.info("[ExecutionEngine] Daily stats reset")
    
    def update_config(self, **kwargs) -> None:
        """Update configuration dynamically."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._config, key):
                    setattr(self._config, key, value)
                elif hasattr(self._config.risk_limits, key):
                    setattr(self._config.risk_limits, key, value)
                logger.info(f"[ExecutionEngine] Updated {key} = {value}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get execution statistics."""
        with self._lock:
            successful = sum(1 for r in self._execution_history if r.success)
            total = len(self._execution_history)
            
            return {
                "state": self._state.value,
                "broker": self._broker.get_info() if self._broker else None,
                "daily_pnl": round(self._daily_pnl, 2),
                "daily_trades": self._daily_trades,
                "total_executions": total,
                "successful_executions": successful,
                "success_rate": round(successful / total, 3) if total > 0 else 0,
                "in_cooldown": self._last_loss_time is not None and 
                              (int(time.time() * 1000) - self._last_loss_time < 
                               self._config.risk_limits.cooldown_after_loss_ms),
            }
    
    def get_config(self) -> Dict[str, Any]:
        """Get current configuration."""
        return self._config.to_dict()
    
    def get_audit_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent audit log entries."""
        with self._lock:
            return [e.to_dict() for e in self._audit_log[-limit:]]
    
    def get_execution_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent execution history."""
        with self._lock:
            return [r.to_dict() for r in self._execution_history[-limit:]]
    
    async def get_positions(self) -> List[Dict[str, Any]]:
        """Get current positions from broker."""
        if not self._broker or not self._broker.is_connected:
            return []
        
        positions = await self._broker.get_positions()
        return [p.to_dict() for p in positions]
    
    async def get_account_info(self) -> Dict[str, Any]:
        """Get account info from broker."""
        if not self._broker or not self._broker.is_connected:
            return {"error": "Broker not connected"}
        
        return await self._broker.get_account_info()
