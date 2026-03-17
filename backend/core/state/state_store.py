"""
Centralized State Store

Single source of truth for all market data, arbitrage state, and session information.
Thread-safe with async support for concurrent access from multiple components.

Design Principles:
- Immutable snapshots for consumers
- Lock-free reads where possible
- Atomic updates with version tracking
- Event-driven change notifications
"""

import time
import asyncio
import logging
from typing import Dict, List, Optional, Any, Callable, Set
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections import defaultdict
from threading import RLock
from enum import Enum

logger = logging.getLogger(__name__)


class SessionType(Enum):
    """FX trading session types."""
    SYDNEY = "sydney"
    TOKYO = "tokyo"
    LONDON = "london"
    NEW_YORK = "new_york"
    OFF_HOURS = "off_hours"


class SessionOverlap(Enum):
    """High-liquidity session overlaps."""
    NONE = "none"
    SYDNEY_TOKYO = "sydney_tokyo"
    TOKYO_LONDON = "tokyo_london"
    LONDON_NY = "london_ny"  # Highest liquidity


@dataclass
class SourceState:
    """
    State for a single data source for a symbol.
    
    Attributes:
        source_id: Unique identifier for the data source
        symbol: Currency pair symbol
        bid: Current bid price
        ask: Current ask price
        mid: Mid-market price
        spread_pips: Spread in pips
        timestamp_ms: Server timestamp in milliseconds
        receive_time_ms: Local receive time
        latency_ms: Estimated latency
        is_stale: Whether data is considered stale
        tick_count: Number of ticks received
    """
    source_id: str
    symbol: str
    bid: float = 0.0
    ask: float = 0.0
    mid: float = 0.0
    spread_pips: float = 0.0
    timestamp_ms: int = 0
    receive_time_ms: int = 0
    latency_ms: float = 0.0
    is_stale: bool = True
    tick_count: int = 0
    last_update: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def update(self, bid: float, ask: float, timestamp_ms: int, 
               pip_value: float = 0.0001) -> None:
        """Update source state with new tick data."""
        self.bid = bid
        self.ask = ask
        self.mid = (bid + ask) / 2
        self.spread_pips = (ask - bid) / pip_value
        self.timestamp_ms = timestamp_ms
        self.receive_time_ms = int(time.time() * 1000)
        self.latency_ms = self.receive_time_ms - timestamp_ms
        self.is_stale = False
        self.tick_count += 1
        self.last_update = datetime.now(timezone.utc)
    
    def mark_stale(self) -> None:
        """Mark this source as stale."""
        self.is_stale = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "source_id": self.source_id,
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "mid": self.mid,
            "spread_pips": round(self.spread_pips, 2),
            "timestamp_ms": self.timestamp_ms,
            "latency_ms": round(self.latency_ms, 1),
            "is_stale": self.is_stale,
            "tick_count": self.tick_count,
        }


@dataclass
class AlignedBatch:
    """
    Time-aligned micro-batch of ticks from multiple sources.
    
    Attributes:
        window_start_ms: Start of alignment window
        window_end_ms: End of alignment window
        sources: Dict of source_id -> SourceState snapshot
        is_complete: Whether all expected sources reported
        created_at: When this batch was created
    """
    window_start_ms: int
    window_end_ms: int
    sources: Dict[str, SourceState] = field(default_factory=dict)
    is_complete: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def add_source(self, source_state: SourceState) -> None:
        """Add a source snapshot to this batch."""
        # Create a copy to preserve immutability
        self.sources[source_state.source_id] = SourceState(
            source_id=source_state.source_id,
            symbol=source_state.symbol,
            bid=source_state.bid,
            ask=source_state.ask,
            mid=source_state.mid,
            spread_pips=source_state.spread_pips,
            timestamp_ms=source_state.timestamp_ms,
            receive_time_ms=source_state.receive_time_ms,
            latency_ms=source_state.latency_ms,
            is_stale=source_state.is_stale,
            tick_count=source_state.tick_count,
        )
    
    def get_best_bid(self) -> Optional[tuple]:
        """Get best bid price and source."""
        if not self.sources:
            return None
        valid = [(s.source_id, s.bid) for s in self.sources.values() if not s.is_stale]
        if not valid:
            return None
        return max(valid, key=lambda x: x[1])
    
    def get_best_ask(self) -> Optional[tuple]:
        """Get best ask price and source."""
        if not self.sources:
            return None
        valid = [(s.source_id, s.ask) for s in self.sources.values() if not s.is_stale]
        if not valid:
            return None
        return min(valid, key=lambda x: x[1])
    
    def get_cross_spread_pips(self, pip_value: float = 0.0001) -> Optional[float]:
        """Calculate cross-source spread in pips."""
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if not best_bid or not best_ask:
            return None
        return (best_ask[1] - best_bid[1]) / pip_value


@dataclass
class ArbitrageSnapshot:
    """
    Current arbitrage state for a symbol.
    
    Attributes:
        symbol: Currency pair
        state: Current state machine state
        cross_spread_pips: Cross-source spread
        profit_potential_pips: Estimated profit if negative spread
        buy_source: Source to buy from
        sell_source: Source to sell to
        confidence: Confidence score 0-1
        stability: Stability score 0-1
        time_in_state_ms: How long in current state
        last_opportunity_time: When last opportunity was detected
    """
    symbol: str
    state: str = "IDLE"
    cross_spread_pips: float = 0.0
    profit_potential_pips: float = 0.0
    buy_source: Optional[str] = None
    sell_source: Optional[str] = None
    confidence: float = 0.0
    stability: float = 0.0
    time_in_state_ms: int = 0
    last_opportunity_time: Optional[datetime] = None
    state_entered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "symbol": self.symbol,
            "state": self.state,
            "cross_spread_pips": round(self.cross_spread_pips, 3),
            "profit_potential_pips": round(self.profit_potential_pips, 3),
            "buy_source": self.buy_source,
            "sell_source": self.sell_source,
            "confidence": round(self.confidence, 3),
            "stability": round(self.stability, 3),
            "time_in_state_ms": self.time_in_state_ms,
            "last_opportunity_time": self.last_opportunity_time.isoformat() if self.last_opportunity_time else None,
        }


@dataclass 
class SymbolState:
    """
    Complete state for a single symbol across all sources.
    
    Aggregates source states, aligned batches, and arbitrage state.
    """
    symbol: str
    sources: Dict[str, SourceState] = field(default_factory=dict)
    current_batch: Optional[AlignedBatch] = None
    recent_batches: List[AlignedBatch] = field(default_factory=list)
    arbitrage: ArbitrageSnapshot = None
    pip_value: float = 0.0001
    max_batch_history: int = 100
    
    def __post_init__(self):
        if self.arbitrage is None:
            self.arbitrage = ArbitrageSnapshot(symbol=self.symbol)
        # Adjust pip value for JPY pairs
        if "JPY" in self.symbol:
            self.pip_value = 0.01
    
    def get_or_create_source(self, source_id: str) -> SourceState:
        """Get or create a source state."""
        if source_id not in self.sources:
            self.sources[source_id] = SourceState(source_id=source_id, symbol=self.symbol)
        return self.sources[source_id]
    
    def add_batch(self, batch: AlignedBatch) -> None:
        """Add a completed batch to history."""
        self.recent_batches.append(batch)
        if len(self.recent_batches) > self.max_batch_history:
            self.recent_batches.pop(0)
    
    def get_source_comparison(self) -> Dict[str, Any]:
        """Get comparison data for all sources."""
        if not self.sources:
            return {"symbol": self.symbol, "sources": [], "error": "No sources"}
        
        source_list = [s.to_dict() for s in self.sources.values() if not s.is_stale]
        if len(source_list) < 2:
            return {"symbol": self.symbol, "sources": source_list, "insufficient": True}
        
        # Calculate cross-source metrics
        bids = [s.bid for s in self.sources.values() if not s.is_stale]
        asks = [s.ask for s in self.sources.values() if not s.is_stale]
        
        best_bid = max(bids) if bids else 0
        best_ask = min(asks) if asks else 0
        cross_spread = (best_ask - best_bid) / self.pip_value
        
        return {
            "symbol": self.symbol,
            "sources": source_list,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "cross_spread_pips": round(cross_spread, 3),
            "opportunity": cross_spread < 0,
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "symbol": self.symbol,
            "source_count": len(self.sources),
            "sources": {sid: s.to_dict() for sid, s in self.sources.items()},
            "arbitrage": self.arbitrage.to_dict() if self.arbitrage else None,
            "batch_count": len(self.recent_batches),
        }


@dataclass
class SessionState:
    """
    Current trading session state.
    
    Tracks active sessions, overlaps, and session-specific metrics.
    """
    primary_session: SessionType = SessionType.OFF_HOURS
    active_sessions: List[SessionType] = field(default_factory=list)
    overlap: SessionOverlap = SessionOverlap.NONE
    utc_hour: int = 0
    high_liquidity: bool = False
    expected_spread_factor: float = 1.0
    expected_volatility: str = "normal"
    
    def update(self) -> None:
        """Update session state based on current UTC time."""
        now = datetime.now(timezone.utc)
        self.utc_hour = now.hour
        
        # Determine active sessions
        self.active_sessions = []
        
        # Sydney: 21:00-06:00 UTC
        if self.utc_hour >= 21 or self.utc_hour < 6:
            self.active_sessions.append(SessionType.SYDNEY)
        
        # Tokyo: 00:00-09:00 UTC
        if 0 <= self.utc_hour < 9:
            self.active_sessions.append(SessionType.TOKYO)
        
        # London: 07:00-16:00 UTC
        if 7 <= self.utc_hour < 16:
            self.active_sessions.append(SessionType.LONDON)
        
        # New York: 12:00-21:00 UTC
        if 12 <= self.utc_hour < 21:
            self.active_sessions.append(SessionType.NEW_YORK)
        
        # Determine overlap
        self.overlap = SessionOverlap.NONE
        if SessionType.LONDON in self.active_sessions and SessionType.NEW_YORK in self.active_sessions:
            self.overlap = SessionOverlap.LONDON_NY
            self.high_liquidity = True
            self.expected_spread_factor = 0.8
            self.expected_volatility = "high"
        elif SessionType.TOKYO in self.active_sessions and SessionType.LONDON in self.active_sessions:
            self.overlap = SessionOverlap.TOKYO_LONDON
            self.high_liquidity = True
            self.expected_spread_factor = 0.9
            self.expected_volatility = "moderate"
        elif SessionType.SYDNEY in self.active_sessions and SessionType.TOKYO in self.active_sessions:
            self.overlap = SessionOverlap.SYDNEY_TOKYO
            self.high_liquidity = False
            self.expected_spread_factor = 1.1
            self.expected_volatility = "low"
        else:
            self.high_liquidity = len(self.active_sessions) > 0
            self.expected_spread_factor = 1.0 if self.active_sessions else 1.3
            self.expected_volatility = "normal" if self.active_sessions else "low"
        
        # Set primary session
        if self.overlap != SessionOverlap.NONE:
            self.primary_session = SessionType.LONDON if SessionType.LONDON in self.active_sessions else self.active_sessions[0]
        elif self.active_sessions:
            self.primary_session = self.active_sessions[0]
        else:
            self.primary_session = SessionType.OFF_HOURS
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "primary_session": self.primary_session.value,
            "active_sessions": [s.value for s in self.active_sessions],
            "overlap": self.overlap.value,
            "utc_hour": self.utc_hour,
            "high_liquidity": self.high_liquidity,
            "expected_spread_factor": self.expected_spread_factor,
            "expected_volatility": self.expected_volatility,
        }


class StateStore:
    """
    Centralized state store for the FX arbitrage platform.
    
    Provides:
    - Thread-safe access to all market data
    - Immutable snapshots for consumers
    - Event-driven change notifications
    - Atomic updates with version tracking
    
    Usage:
        store = StateStore()
        store.update_tick("EURUSD", "mt5_primary", bid=1.0850, ask=1.0852, timestamp_ms=...)
        
        # Get immutable snapshot
        snapshot = store.get_symbol_state("EURUSD")
        
        # Subscribe to changes
        store.subscribe("tick", my_callback)
    """
    
    def __init__(self, alignment_window_ms: int = 20, stale_threshold_ms: int = 5000):
        """
        Initialize the state store.
        
        Args:
            alignment_window_ms: Size of time alignment windows
            stale_threshold_ms: Time after which data is considered stale
        """
        self._lock = RLock()
        self._symbols: Dict[str, SymbolState] = {}
        self._session = SessionState()
        self._expected_sources: Set[str] = set()
        self._alignment_window_ms = alignment_window_ms
        self._stale_threshold_ms = stale_threshold_ms
        self._version = 0
        
        # Event subscribers
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        
        # Metrics
        self._total_ticks = 0
        self._total_opportunities = 0
        self._start_time = datetime.now(timezone.utc)
        
        # Update session state
        self._session.update()
        
        logger.info(f"[StateStore] Initialized with {alignment_window_ms}ms alignment windows")
    
    def add_expected_source(self, source_id: str) -> None:
        """Register an expected data source."""
        with self._lock:
            self._expected_sources.add(source_id)
            logger.info(f"[StateStore] Added expected source: {source_id}")
    
    def add_symbol(self, symbol: str) -> SymbolState:
        """Add a symbol to track."""
        with self._lock:
            if symbol not in self._symbols:
                self._symbols[symbol] = SymbolState(symbol=symbol)
                logger.info(f"[StateStore] Added symbol: {symbol}")
            return self._symbols[symbol]
    
    def update_tick(self, symbol: str, source_id: str, 
                    bid: float, ask: float, timestamp_ms: int) -> None:
        """
        Update state with a new tick.
        
        This is the primary entry point for new market data.
        
        Args:
            symbol: Currency pair symbol
            source_id: Data source identifier
            bid: Bid price
            ask: Ask price
            timestamp_ms: Server timestamp in milliseconds
        """
        with self._lock:
            # Ensure symbol exists
            if symbol not in self._symbols:
                self.add_symbol(symbol)
            
            symbol_state = self._symbols[symbol]
            source_state = symbol_state.get_or_create_source(source_id)
            
            # Update source state
            source_state.update(bid, ask, timestamp_ms, symbol_state.pip_value)
            
            # Update alignment batch
            self._update_alignment_batch(symbol_state, source_state)
            
            # Increment counters
            self._total_ticks += 1
            self._version += 1
            
            # Notify subscribers
            self._notify("tick", {
                "symbol": symbol,
                "source_id": source_id,
                "bid": bid,
                "ask": ask,
                "timestamp_ms": timestamp_ms,
            })
    
    def _update_alignment_batch(self, symbol_state: SymbolState, 
                                 source_state: SourceState) -> None:
        """Update the current alignment batch with new source data."""
        now_ms = int(time.time() * 1000)
        window_start = (now_ms // self._alignment_window_ms) * self._alignment_window_ms
        window_end = window_start + self._alignment_window_ms
        
        # Check if we need a new batch
        if (symbol_state.current_batch is None or 
            symbol_state.current_batch.window_start_ms != window_start):
            
            # Complete the old batch if it exists
            if symbol_state.current_batch is not None:
                symbol_state.current_batch.is_complete = True
                symbol_state.add_batch(symbol_state.current_batch)
                
                # Notify batch complete
                self._notify("batch_complete", {
                    "symbol": symbol_state.symbol,
                    "batch": symbol_state.current_batch,
                })
            
            # Create new batch
            symbol_state.current_batch = AlignedBatch(
                window_start_ms=window_start,
                window_end_ms=window_end,
            )
        
        # Add source to current batch
        symbol_state.current_batch.add_source(source_state)
        
        # Check if batch is complete (all expected sources reported)
        if len(symbol_state.current_batch.sources) >= len(self._expected_sources):
            symbol_state.current_batch.is_complete = True
    
    def update_arbitrage_state(self, symbol: str, state: str, 
                                cross_spread_pips: float, profit_pips: float,
                                buy_source: Optional[str], sell_source: Optional[str],
                                confidence: float, stability: float) -> None:
        """
        Update arbitrage state for a symbol.
        
        Called by the ArbitrageStateMachine when state changes.
        """
        with self._lock:
            if symbol not in self._symbols:
                return
            
            arb = self._symbols[symbol].arbitrage
            old_state = arb.state
            
            arb.state = state
            arb.cross_spread_pips = cross_spread_pips
            arb.profit_potential_pips = profit_pips
            arb.buy_source = buy_source
            arb.sell_source = sell_source
            arb.confidence = confidence
            arb.stability = stability
            
            if state != old_state:
                arb.state_entered_at = datetime.now(timezone.utc)
                arb.time_in_state_ms = 0
            else:
                arb.time_in_state_ms = int((datetime.now(timezone.utc) - arb.state_entered_at).total_seconds() * 1000)
            
            if state == "CONFIRMED" and profit_pips > 0:
                arb.last_opportunity_time = datetime.now(timezone.utc)
                self._total_opportunities += 1
            
            self._version += 1
            
            # Notify subscribers
            self._notify("arbitrage_state", {
                "symbol": symbol,
                "old_state": old_state,
                "new_state": state,
                "arbitrage": arb.to_dict(),
            })
    
    def update_session(self) -> SessionState:
        """Update and return current session state."""
        with self._lock:
            self._session.update()
            return self._session
    
    def get_symbol_state(self, symbol: str) -> Optional[SymbolState]:
        """Get immutable snapshot of symbol state."""
        with self._lock:
            return self._symbols.get(symbol)
    
    def get_all_symbols(self) -> Dict[str, SymbolState]:
        """Get all symbol states."""
        with self._lock:
            return dict(self._symbols)
    
    def get_session_state(self) -> SessionState:
        """Get current session state."""
        with self._lock:
            return self._session
    
    def get_source_comparison(self, symbol: str) -> Dict[str, Any]:
        """Get source comparison for a symbol."""
        with self._lock:
            if symbol not in self._symbols:
                return {"symbol": symbol, "error": "Symbol not found"}
            return self._symbols[symbol].get_source_comparison()
    
    def get_global_stats(self) -> Dict[str, Any]:
        """Get global statistics."""
        with self._lock:
            uptime = (datetime.now(timezone.utc) - self._start_time).total_seconds()
            return {
                "version": self._version,
                "total_ticks": self._total_ticks,
                "total_opportunities": self._total_opportunities,
                "symbols_tracked": len(self._symbols),
                "sources_expected": len(self._expected_sources),
                "uptime_seconds": round(uptime, 1),
                "ticks_per_second": round(self._total_ticks / max(uptime, 1), 2),
                "session": self._session.to_dict(),
            }
    
    def get_full_state(self) -> Dict[str, Any]:
        """Get complete state snapshot for debugging/export."""
        with self._lock:
            return {
                "version": self._version,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "session": self._session.to_dict(),
                "symbols": {s: state.to_dict() for s, state in self._symbols.items()},
                "stats": self.get_global_stats(),
            }
    
    def mark_stale_sources(self) -> List[str]:
        """Mark sources as stale if no recent updates. Returns list of stale sources."""
        stale = []
        now_ms = int(time.time() * 1000)
        
        with self._lock:
            for symbol_state in self._symbols.values():
                for source_state in symbol_state.sources.values():
                    if not source_state.is_stale:
                        age_ms = now_ms - source_state.receive_time_ms
                        if age_ms > self._stale_threshold_ms:
                            source_state.mark_stale()
                            stale.append(f"{symbol_state.symbol}:{source_state.source_id}")
        
        return stale
    
    def subscribe(self, event_type: str, callback: Callable) -> None:
        """
        Subscribe to state change events.
        
        Event types:
        - "tick": New tick received
        - "batch_complete": Alignment batch completed
        - "arbitrage_state": Arbitrage state changed
        """
        with self._lock:
            self._subscribers[event_type].append(callback)
    
    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """Unsubscribe from events."""
        with self._lock:
            if callback in self._subscribers[event_type]:
                self._subscribers[event_type].remove(callback)
    
    def _notify(self, event_type: str, data: Dict[str, Any]) -> None:
        """Notify subscribers of an event."""
        callbacks = self._subscribers.get(event_type, [])
        for callback in callbacks:
            try:
                callback(event_type, data)
            except Exception as e:
                logger.error(f"[StateStore] Subscriber error: {e}")
