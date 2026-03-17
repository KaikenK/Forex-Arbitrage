"""
Arbitrage State Machine

Replaces tick-level arbitrage emission with a state-based approach.
Requires condition persistence over configurable time windows before
confirming opportunities. Enforces cooldown to prevent repeated emissions.

State Transitions:
    IDLE → CANDIDATE: Potential opportunity detected
    CANDIDATE → CONFIRMED: Conditions persist over confirmation window
    CANDIDATE → IDLE: Conditions no longer met
    CONFIRMED → COOLING: Opportunity emitted, enter cooldown
    COOLING → IDLE: Cooldown period complete
    Any → INVALIDATED: Error or force reset

Design Principles:
- No opportunity emission without confirmation
- Configurable timing for research flexibility
- Full state history for reproducibility
- Thread-safe operation
"""

import time
import logging
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import RLock

logger = logging.getLogger(__name__)


class ArbitrageState(Enum):
    """Arbitrage state machine states."""
    IDLE = "IDLE"                   # No opportunity detected
    CANDIDATE = "CANDIDATE"         # Potential opportunity, awaiting confirmation
    CONFIRMED = "CONFIRMED"         # Confirmed opportunity, ready for execution
    COOLING = "COOLING"             # Post-emission cooldown
    INVALIDATED = "INVALIDATED"     # Error state, requires reset


@dataclass
class StateMachineConfig:
    """
    Configuration for the arbitrage state machine.
    
    Attributes:
        confirmation_window_ms: Time conditions must persist for confirmation
        cooldown_period_ms: Minimum time between opportunity emissions
        min_profit_pips: Minimum profit threshold for candidate detection
        min_confidence: Minimum confidence score for candidate detection
        min_stability: Minimum stability score for confirmation
        max_candidate_age_ms: Maximum time in CANDIDATE before auto-rejection
        enable_auto_reset: Whether to auto-reset from INVALIDATED
    """
    confirmation_window_ms: int = 100
    cooldown_period_ms: int = 500
    min_profit_pips: float = 0.1
    min_confidence: float = 0.5
    min_stability: float = 0.3
    max_candidate_age_ms: int = 1000
    enable_auto_reset: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "confirmation_window_ms": self.confirmation_window_ms,
            "cooldown_period_ms": self.cooldown_period_ms,
            "min_profit_pips": self.min_profit_pips,
            "min_confidence": self.min_confidence,
            "min_stability": self.min_stability,
            "max_candidate_age_ms": self.max_candidate_age_ms,
            "enable_auto_reset": self.enable_auto_reset,
        }


@dataclass
class ArbitrageCandidate:
    """
    A potential arbitrage opportunity being tracked.
    
    Attributes:
        symbol: Currency pair
        buy_source: Source to buy from
        sell_source: Source to sell to
        profit_pips: Estimated profit in pips
        confidence: Confidence score
        stability: Stability score (increases with persistence)
        detected_at_ms: When first detected
        readings: List of (timestamp, profit_pips) readings
    """
    symbol: str
    buy_source: str
    sell_source: str
    profit_pips: float
    confidence: float
    stability: float = 0.0
    detected_at_ms: int = 0
    last_update_ms: int = 0
    readings: List[tuple] = field(default_factory=list)
    
    def __post_init__(self):
        if self.detected_at_ms == 0:
            self.detected_at_ms = int(time.time() * 1000)
        self.last_update_ms = self.detected_at_ms
    
    def add_reading(self, profit_pips: float, confidence: float) -> None:
        """Add a new reading to track persistence."""
        now_ms = int(time.time() * 1000)
        self.readings.append((now_ms, profit_pips, confidence))
        self.last_update_ms = now_ms
        self.profit_pips = profit_pips
        self.confidence = confidence
        
        # Keep only recent readings (last 2 seconds)
        cutoff = now_ms - 2000
        self.readings = [(t, p, c) for t, p, c in self.readings if t > cutoff]
        
        # Update stability based on consistency
        self._update_stability()
    
    def _update_stability(self) -> None:
        """Calculate stability based on reading consistency."""
        if len(self.readings) < 2:
            self.stability = 0.0
            return
        
        # Stability increases with:
        # 1. Number of consistent readings
        # 2. Low variance in profit_pips
        # 3. Time spent in candidate state
        
        profits = [p for _, p, _ in self.readings]
        mean_profit = sum(profits) / len(profits)
        
        # Calculate coefficient of variation
        if mean_profit > 0:
            variance = sum((p - mean_profit) ** 2 for p in profits) / len(profits)
            std_dev = variance ** 0.5
            cv = std_dev / mean_profit
        else:
            cv = 1.0
        
        # Stability: low CV = high stability
        cv_score = max(0, 1 - cv)
        
        # Reading count score (more readings = more stable, caps at 10)
        count_score = min(len(self.readings) / 10, 1.0)
        
        # Time score (longer in state = more stable, caps at 500ms)
        age_ms = self.last_update_ms - self.detected_at_ms
        time_score = min(age_ms / 500, 1.0)
        
        # Weighted combination
        self.stability = 0.4 * cv_score + 0.3 * count_score + 0.3 * time_score
    
    def age_ms(self) -> int:
        """Get age of this candidate in milliseconds."""
        return int(time.time() * 1000) - self.detected_at_ms
    
    def time_since_update_ms(self) -> int:
        """Get time since last update in milliseconds."""
        return int(time.time() * 1000) - self.last_update_ms
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "buy_source": self.buy_source,
            "sell_source": self.sell_source,
            "profit_pips": round(self.profit_pips, 3),
            "confidence": round(self.confidence, 3),
            "stability": round(self.stability, 3),
            "age_ms": self.age_ms(),
            "reading_count": len(self.readings),
        }


@dataclass
class ArbitrageStateTransition:
    """
    Record of a state transition for history/debugging.
    """
    symbol: str
    from_state: ArbitrageState
    to_state: ArbitrageState
    timestamp: datetime
    reason: str
    candidate: Optional[ArbitrageCandidate] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "timestamp": self.timestamp.isoformat(),
            "reason": self.reason,
            "candidate": self.candidate.to_dict() if self.candidate else None,
        }


class SymbolStateMachine:
    """
    State machine for a single symbol's arbitrage detection.
    """
    
    def __init__(self, symbol: str, config: StateMachineConfig):
        self.symbol = symbol
        self.config = config
        self._state = ArbitrageState.IDLE
        self._candidate: Optional[ArbitrageCandidate] = None
        self._state_entered_at: int = int(time.time() * 1000)
        self._cooldown_until: int = 0
        self._last_emission: Optional[ArbitrageCandidate] = None
        self._transition_history: List[ArbitrageStateTransition] = []
        self._max_history = 100
    
    @property
    def state(self) -> ArbitrageState:
        return self._state
    
    @property
    def candidate(self) -> Optional[ArbitrageCandidate]:
        return self._candidate
    
    def time_in_state_ms(self) -> int:
        """Get time in current state."""
        return int(time.time() * 1000) - self._state_entered_at
    
    def process_batch(self, cross_spread_pips: float, profit_pips: float,
                      buy_source: Optional[str], sell_source: Optional[str],
                      confidence: float) -> Optional[ArbitrageCandidate]:
        """
        Process an aligned batch and update state.
        
        Returns confirmed candidate if transitioning to CONFIRMED, else None.
        
        Args:
            cross_spread_pips: Cross-source spread in pips
            profit_pips: Potential profit in pips (negative spread)
            buy_source: Source to buy from
            sell_source: Source to sell to
            confidence: Confidence score from engine
        
        Returns:
            ArbitrageCandidate if confirmed, else None
        """
        now_ms = int(time.time() * 1000)
        
        # Handle state-specific logic
        if self._state == ArbitrageState.IDLE:
            return self._handle_idle(profit_pips, buy_source, sell_source, confidence)
        
        elif self._state == ArbitrageState.CANDIDATE:
            return self._handle_candidate(profit_pips, buy_source, sell_source, confidence)
        
        elif self._state == ArbitrageState.CONFIRMED:
            return self._handle_confirmed()
        
        elif self._state == ArbitrageState.COOLING:
            return self._handle_cooling(now_ms)
        
        elif self._state == ArbitrageState.INVALIDATED:
            return self._handle_invalidated()
        
        return None
    
    def _handle_idle(self, profit_pips: float, buy_source: Optional[str],
                     sell_source: Optional[str], confidence: float) -> Optional[ArbitrageCandidate]:
        """Handle IDLE state - look for new candidates."""
        # Check if conditions meet minimum thresholds
        if (profit_pips >= self.config.min_profit_pips and 
            confidence >= self.config.min_confidence and
            buy_source and sell_source):
            
            # Create new candidate
            self._candidate = ArbitrageCandidate(
                symbol=self.symbol,
                buy_source=buy_source,
                sell_source=sell_source,
                profit_pips=profit_pips,
                confidence=confidence,
            )
            
            self._transition(ArbitrageState.CANDIDATE, 
                           f"Potential opportunity: {profit_pips:.3f} pips")
        
        return None
    
    def _handle_candidate(self, profit_pips: float, buy_source: Optional[str],
                          sell_source: Optional[str], confidence: float) -> Optional[ArbitrageCandidate]:
        """Handle CANDIDATE state - track persistence and confirm if stable."""
        
        # Check if conditions still met
        if profit_pips < self.config.min_profit_pips or confidence < self.config.min_confidence:
            self._transition(ArbitrageState.IDLE, 
                           f"Conditions no longer met: profit={profit_pips:.3f}, conf={confidence:.3f}")
            self._candidate = None
            return None
        
        # Check if sources changed (invalidates candidate)
        if (buy_source != self._candidate.buy_source or 
            sell_source != self._candidate.sell_source):
            # New candidate with different sources
            self._candidate = ArbitrageCandidate(
                symbol=self.symbol,
                buy_source=buy_source,
                sell_source=sell_source,
                profit_pips=profit_pips,
                confidence=confidence,
            )
            return None
        
        # Update candidate with new reading
        self._candidate.add_reading(profit_pips, confidence)
        
        # Check for timeout
        if self._candidate.age_ms() > self.config.max_candidate_age_ms:
            self._transition(ArbitrageState.IDLE, 
                           f"Candidate timed out after {self._candidate.age_ms()}ms")
            self._candidate = None
            return None
        
        # Check for confirmation
        if (self._candidate.age_ms() >= self.config.confirmation_window_ms and
            self._candidate.stability >= self.config.min_stability):
            
            self._transition(ArbitrageState.CONFIRMED,
                           f"Confirmed after {self._candidate.age_ms()}ms, stability={self._candidate.stability:.3f}")
            return self._candidate
        
        return None
    
    def _handle_confirmed(self) -> Optional[ArbitrageCandidate]:
        """Handle CONFIRMED state - emit and enter cooldown."""
        confirmed = self._candidate
        self._last_emission = confirmed
        self._cooldown_until = int(time.time() * 1000) + self.config.cooldown_period_ms
        
        self._transition(ArbitrageState.COOLING,
                        f"Entering cooldown for {self.config.cooldown_period_ms}ms")
        
        return None  # Already returned on transition to CONFIRMED
    
    def _handle_cooling(self, now_ms: int) -> Optional[ArbitrageCandidate]:
        """Handle COOLING state - wait for cooldown to complete."""
        if now_ms >= self._cooldown_until:
            self._transition(ArbitrageState.IDLE, "Cooldown complete")
            self._candidate = None
        
        return None
    
    def _handle_invalidated(self) -> Optional[ArbitrageCandidate]:
        """Handle INVALIDATED state - reset if auto-reset enabled."""
        if self.config.enable_auto_reset:
            self._transition(ArbitrageState.IDLE, "Auto-reset from INVALIDATED")
            self._candidate = None
        
        return None
    
    def _transition(self, to_state: ArbitrageState, reason: str) -> None:
        """Record a state transition."""
        transition = ArbitrageStateTransition(
            symbol=self.symbol,
            from_state=self._state,
            to_state=to_state,
            timestamp=datetime.now(timezone.utc),
            reason=reason,
            candidate=self._candidate,
        )
        
        self._transition_history.append(transition)
        if len(self._transition_history) > self._max_history:
            self._transition_history.pop(0)
        
        logger.debug(f"[StateMachine:{self.symbol}] {self._state.value} → {to_state.value}: {reason}")
        
        self._state = to_state
        self._state_entered_at = int(time.time() * 1000)
    
    def force_reset(self, reason: str = "Manual reset") -> None:
        """Force reset to IDLE state."""
        self._transition(ArbitrageState.IDLE, reason)
        self._candidate = None
    
    def invalidate(self, reason: str = "Error") -> None:
        """Transition to INVALIDATED state."""
        self._transition(ArbitrageState.INVALIDATED, reason)
    
    def get_state_info(self) -> Dict[str, Any]:
        """Get current state information."""
        return {
            "symbol": self.symbol,
            "state": self._state.value,
            "time_in_state_ms": self.time_in_state_ms(),
            "candidate": self._candidate.to_dict() if self._candidate else None,
            "last_emission": self._last_emission.to_dict() if self._last_emission else None,
            "cooldown_remaining_ms": max(0, self._cooldown_until - int(time.time() * 1000)),
        }
    
    def get_transition_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent transition history."""
        return [t.to_dict() for t in self._transition_history[-limit:]]


class ArbitrageStateMachine:
    """
    Master state machine managing arbitrage detection for all symbols.
    
    Coordinates individual symbol state machines and provides a unified
    interface for the execution pipeline.
    
    Usage:
        config = StateMachineConfig(confirmation_window_ms=100)
        machine = ArbitrageStateMachine(config)
        machine.add_symbol("EURUSD")
        
        # Process aligned batch
        confirmed = machine.process_batch("EURUSD", ...)
        if confirmed:
            # Execute or log
    """
    
    def __init__(self, config: StateMachineConfig, 
                 on_confirmed: Optional[Callable] = None):
        """
        Initialize the master state machine.
        
        Args:
            config: Configuration for all symbol state machines
            on_confirmed: Callback when opportunity is confirmed
        """
        self._lock = RLock()
        self._config = config
        self._machines: Dict[str, SymbolStateMachine] = {}
        self._on_confirmed = on_confirmed
        self._total_confirmations = 0
        
        logger.info(f"[ArbitrageStateMachine] Initialized with config: {config.to_dict()}")
    
    def add_symbol(self, symbol: str) -> None:
        """Add a symbol to track."""
        with self._lock:
            if symbol not in self._machines:
                self._machines[symbol] = SymbolStateMachine(symbol, self._config)
                logger.info(f"[ArbitrageStateMachine] Added symbol: {symbol}")
    
    def process_batch(self, symbol: str, cross_spread_pips: float, 
                      profit_pips: float, buy_source: Optional[str],
                      sell_source: Optional[str], confidence: float) -> Optional[ArbitrageCandidate]:
        """
        Process an aligned batch for a symbol.
        
        Returns confirmed candidate if an opportunity is confirmed.
        """
        with self._lock:
            if symbol not in self._machines:
                self.add_symbol(symbol)
            
            machine = self._machines[symbol]
            old_state = machine.state
            
            confirmed = machine.process_batch(
                cross_spread_pips=cross_spread_pips,
                profit_pips=profit_pips,
                buy_source=buy_source,
                sell_source=sell_source,
                confidence=confidence,
            )
            
            if confirmed:
                self._total_confirmations += 1
                
                if self._on_confirmed:
                    try:
                        self._on_confirmed(confirmed)
                    except Exception as e:
                        logger.error(f"[ArbitrageStateMachine] Callback error: {e}")
            
            return confirmed
    
    def get_symbol_state(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get state info for a symbol."""
        with self._lock:
            if symbol in self._machines:
                return self._machines[symbol].get_state_info()
            return None
    
    def get_all_states(self) -> Dict[str, Dict[str, Any]]:
        """Get state info for all symbols."""
        with self._lock:
            return {s: m.get_state_info() for s, m in self._machines.items()}
    
    def get_config(self) -> Dict[str, Any]:
        """Get current configuration."""
        return self._config.to_dict()
    
    def update_config(self, **kwargs) -> None:
        """Update configuration dynamically."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._config, key):
                    setattr(self._config, key, value)
                    logger.info(f"[ArbitrageStateMachine] Updated config.{key} = {value}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get aggregate statistics."""
        with self._lock:
            states = {}
            for s, m in self._machines.items():
                state = m.state.value
                states[state] = states.get(state, 0) + 1
            
            return {
                "total_symbols": len(self._machines),
                "total_confirmations": self._total_confirmations,
                "state_distribution": states,
                "config": self._config.to_dict(),
            }
    
    def reset_all(self, reason: str = "Global reset") -> None:
        """Reset all symbol state machines."""
        with self._lock:
            for machine in self._machines.values():
                machine.force_reset(reason)
            logger.info(f"[ArbitrageStateMachine] All machines reset: {reason}")
