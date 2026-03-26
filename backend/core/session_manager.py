"""
Trading Session Manager

Manages FX trading session detection, tracking, and analytics.
Provides session-aware metrics for arbitrage detection research.

Design Decisions:
- Detects overlapping sessions (London/NY overlap is highest liquidity)
- Tracks per-session statistics for spread, latency, arbitrage frequency
- Supports session-based filtering and analysis
- Thread-safe for concurrent access
"""

import time
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections import defaultdict
import threading
import statistics

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """
    Information about a trading session.
    
    Attributes:
        name: Session name (e.g., "tokyo", "london", "new_york")
        display_name: Human-readable name
        start_hour_utc: Session start hour in UTC
        end_hour_utc: Session end hour in UTC
        primary_currencies: Currencies most active in this session
        typical_spread_factor: Relative spread factor (1.0 = normal)
        typical_volatility: Expected volatility level
    """
    name: str
    display_name: str
    start_hour_utc: int
    end_hour_utc: int
    primary_currencies: List[str] = field(default_factory=list)
    typical_spread_factor: float = 1.0
    typical_volatility: str = "normal"


# Standard FX trading sessions
TRADING_SESSIONS = {
    "sydney": SessionInfo(
        name="sydney",
        display_name="Sydney/Pacific",
        start_hour_utc=21,  # 9pm UTC (previous day)
        end_hour_utc=6,
        primary_currencies=["AUD", "NZD", "JPY"],
        typical_spread_factor=1.3,
        typical_volatility="low",
    ),
    "tokyo": SessionInfo(
        name="tokyo",
        display_name="Tokyo/Asia",
        start_hour_utc=0,
        end_hour_utc=9,
        primary_currencies=["JPY", "AUD", "CNH", "INR"],
        typical_spread_factor=1.2,
        typical_volatility="moderate",
    ),
    "london": SessionInfo(
        name="london",
        display_name="London/Europe",
        start_hour_utc=7,
        end_hour_utc=16,
        primary_currencies=["EUR", "GBP", "CHF"],
        typical_spread_factor=0.9,
        typical_volatility="high",
    ),
    "new_york": SessionInfo(
        name="new_york",
        display_name="New York/Americas",
        start_hour_utc=12,
        end_hour_utc=21,
        primary_currencies=["USD", "CAD", "MXN"],
        typical_spread_factor=0.95,
        typical_volatility="high",
    ),
}

# Session overlaps (highest liquidity periods)
SESSION_OVERLAPS = {
    "tokyo_london": {
        "name": "tokyo_london",
        "display_name": "Tokyo/London Overlap",
        "start_hour_utc": 7,
        "end_hour_utc": 9,
        "liquidity": "moderate",
    },
    "london_ny": {
        "name": "london_ny",
        "display_name": "London/NY Overlap",
        "start_hour_utc": 12,
        "end_hour_utc": 16,
        "liquidity": "highest",
    },
}


@dataclass
class SessionStats:
    """Statistics for a trading session."""
    session_name: str
    tick_count: int = 0
    spreads: List[float] = field(default_factory=list)
    latencies: List[float] = field(default_factory=list)
    arbitrage_count: int = 0
    arbitrage_profit_pips: List[float] = field(default_factory=list)
    
    @property
    def avg_spread_pips(self) -> float:
        if not self.spreads:
            return 0.0
        return statistics.mean(self.spreads)
    
    @property
    def median_spread_pips(self) -> float:
        if not self.spreads:
            return 0.0
        return statistics.median(self.spreads)
    
    @property
    def avg_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        return statistics.mean(self.latencies)
    
    @property
    def median_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        return statistics.median(self.latencies)
    
    @property
    def arbitrage_rate(self) -> float:
        """Arbitrage opportunities per 1000 ticks."""
        if self.tick_count == 0:
            return 0.0
        return (self.arbitrage_count / self.tick_count) * 1000
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_name": self.session_name,
            "tick_count": self.tick_count,
            "avg_spread_pips": round(self.avg_spread_pips, 3),
            "median_spread_pips": round(self.median_spread_pips, 3),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "median_latency_ms": round(self.median_latency_ms, 2),
            "arbitrage_count": self.arbitrage_count,
            "arbitrage_rate_per_1k": round(self.arbitrage_rate, 2),
            "total_arbitrage_profit_pips": round(sum(self.arbitrage_profit_pips), 2) if self.arbitrage_profit_pips else 0,
        }


class SessionManager:
    """
    Manages trading session detection and analytics.
    
    Provides:
    - Current session detection with overlap awareness
    - Per-session statistics tracking
    - Session transition event emission
    - Historical session performance analysis
    
    Example:
        manager = SessionManager()
        
        # Get current session
        session = manager.get_current_session()
        print(f"Active: {session['active_sessions']}")
        
        # Record a tick
        manager.record_tick("tokyo", spread_pips=0.8, latency_ms=45.0)
        
        # Get session stats
        stats = manager.get_session_stats("tokyo")
    """
    
    def __init__(self, max_samples: int = 10000):
        """
        Initialize session manager.
        
        Args:
            max_samples: Maximum samples to keep per session for statistics
        """
        self._max_samples = max_samples
        
        # Per-session statistics
        self._session_stats: Dict[str, SessionStats] = {
            name: SessionStats(session_name=name)
            for name in TRADING_SESSIONS.keys()
        }
        
        # Add overlap sessions
        for name in SESSION_OVERLAPS.keys():
            self._session_stats[name] = SessionStats(session_name=name)
        
        # Session transition tracking
        self._last_session: Optional[str] = None
        self._session_transitions: List[Dict[str, Any]] = []
        
        # Thread safety
        self._lock = threading.Lock()
        
        # Callbacks for session transitions
        self._transition_callbacks: List[callable] = []
    
    def get_current_session(self, timestamp_ms: Optional[int] = None) -> Dict[str, Any]:
        """
        Get current trading session info.
        
        Args:
            timestamp_ms: Optional timestamp (uses current time if not provided)
        
        Returns:
            Dict with session information
        """
        if timestamp_ms:
            dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        else:
            dt = datetime.now(timezone.utc)
        
        hour = dt.hour
        minute = dt.minute
        
        # Find active sessions
        active_sessions = []
        for name, info in TRADING_SESSIONS.items():
            if self._is_in_session(hour, info.start_hour_utc, info.end_hour_utc):
                active_sessions.append(name)
        
        # Check for overlaps
        overlaps = []
        for name, overlap in SESSION_OVERLAPS.items():
            if overlap["start_hour_utc"] <= hour < overlap["end_hour_utc"]:
                overlaps.append(name)
        
        # Determine primary session (for single-session display)
        primary_session = self._get_primary_session(active_sessions, overlaps)
        
        # Get session info
        primary_info = TRADING_SESSIONS.get(primary_session, TRADING_SESSIONS["london"])
        
        return {
            "primary_session": primary_session,
            "display_name": primary_info.display_name,
            "active_sessions": active_sessions,
            "overlaps": overlaps,
            "is_overlap": len(overlaps) > 0,
            "hour_utc": hour,
            "minute_utc": minute,
            "timestamp_utc": dt.isoformat(),
            "primary_currencies": primary_info.primary_currencies,
            "expected_spread_factor": primary_info.typical_spread_factor,
            "expected_volatility": primary_info.typical_volatility,
            "liquidity": "highest" if "london_ny" in overlaps else (
                "high" if primary_session in ["london", "new_york"] else "moderate"
            ),
        }
    
    def _is_in_session(self, hour: int, start: int, end: int) -> bool:
        """Check if hour is within session (handles overnight sessions)."""
        if start <= end:
            return start <= hour < end
        else:
            # Overnight session (e.g., Sydney 21:00 - 06:00)
            return hour >= start or hour < end
    
    def _get_primary_session(self, active: List[str], overlaps: List[str]) -> str:
        """Determine primary session from active sessions."""
        # Priority: overlap > London > NY > Tokyo > Sydney
        if "london_ny" in overlaps:
            return "london_ny"
        if "tokyo_london" in overlaps:
            return "tokyo_london"
        
        priority = ["london", "new_york", "tokyo", "sydney"]
        for session in priority:
            if session in active:
                return session
        
        return "closed"
    
    def detect_session(self, timestamp_ms: int) -> str:
        """
        Detect session for a given timestamp (simple version).
        
        Args:
            timestamp_ms: Unix timestamp in milliseconds
        
        Returns:
            Primary session name
        """
        session_info = self.get_current_session(timestamp_ms)
        return session_info["primary_session"]
    
    def record_tick(
        self,
        session: str,
        spread_pips: float,
        latency_ms: Optional[float] = None,
        symbol: Optional[str] = None,
    ) -> None:
        """
        Record tick statistics for a session.
        
        Args:
            session: Session name
            spread_pips: Spread in pips
            latency_ms: Optional latency measurement
            symbol: Optional symbol for per-symbol tracking
        """
        with self._lock:
            if session not in self._session_stats:
                self._session_stats[session] = SessionStats(session_name=session)
            
            stats = self._session_stats[session]
            stats.tick_count += 1
            
            # Maintain max samples
            if len(stats.spreads) >= self._max_samples:
                stats.spreads.pop(0)
            stats.spreads.append(spread_pips)
            
            if latency_ms is not None:
                if len(stats.latencies) >= self._max_samples:
                    stats.latencies.pop(0)
                stats.latencies.append(latency_ms)
    
    def record_arbitrage(
        self,
        session: str,
        profit_pips: float,
        arb_type: Optional[str] = None,
    ) -> None:
        """
        Record an arbitrage opportunity for a session.
        
        Args:
            session: Session name
            profit_pips: Estimated profit in pips
            arb_type: Type of arbitrage
        """
        with self._lock:
            if session not in self._session_stats:
                self._session_stats[session] = SessionStats(session_name=session)
            
            stats = self._session_stats[session]
            stats.arbitrage_count += 1
            
            if len(stats.arbitrage_profit_pips) >= self._max_samples:
                stats.arbitrage_profit_pips.pop(0)
            stats.arbitrage_profit_pips.append(profit_pips)
    
    def get_session_stats(self, session: str) -> Optional[Dict[str, Any]]:
        """Get statistics for a specific session."""
        with self._lock:
            if session in self._session_stats:
                return self._session_stats[session].to_dict()
            return None
    
    def get_all_session_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all sessions."""
        with self._lock:
            return {
                name: stats.to_dict()
                for name, stats in self._session_stats.items()
                if stats.tick_count > 0
            }
    
    def get_session_comparison(self) -> Dict[str, Any]:
        """
        Get comparative analysis across sessions.
        
        Returns insights about session differences for research.
        """
        with self._lock:
            active_sessions = [
                (name, stats) for name, stats in self._session_stats.items()
                if stats.tick_count > 0
            ]
            
            if not active_sessions:
                return {"message": "No session data collected yet"}
            
            # Find best/worst sessions for different metrics
            best_spread = min(active_sessions, key=lambda x: x[1].avg_spread_pips if x[1].spreads else float('inf'))
            worst_spread = max(active_sessions, key=lambda x: x[1].avg_spread_pips if x[1].spreads else 0)
            most_arbitrage = max(active_sessions, key=lambda x: x[1].arbitrage_rate)
            lowest_latency = min(active_sessions, key=lambda x: x[1].avg_latency_ms if x[1].latencies else float('inf'))
            
            return {
                "sessions_analyzed": len(active_sessions),
                "total_ticks": sum(s[1].tick_count for s in active_sessions),
                "total_arbitrage_opportunities": sum(s[1].arbitrage_count for s in active_sessions),
                "best_spread_session": {
                    "session": best_spread[0],
                    "avg_spread_pips": round(best_spread[1].avg_spread_pips, 3),
                },
                "worst_spread_session": {
                    "session": worst_spread[0],
                    "avg_spread_pips": round(worst_spread[1].avg_spread_pips, 3),
                },
                "most_arbitrage_session": {
                    "session": most_arbitrage[0],
                    "arbitrage_rate_per_1k": round(most_arbitrage[1].arbitrage_rate, 2),
                },
                "lowest_latency_session": {
                    "session": lowest_latency[0],
                    "avg_latency_ms": round(lowest_latency[1].avg_latency_ms, 2) if lowest_latency[1].latencies else None,
                },
            }
    
    def on_session_transition(self, callback: callable) -> None:
        """Register callback for session transitions."""
        self._transition_callbacks.append(callback)
    
    def check_session_transition(self) -> Optional[Dict[str, Any]]:
        """
        Check if session has transitioned and emit event if so.
        
        Returns:
            Transition event dict if transition occurred, None otherwise
        """
        current = self.get_current_session()
        primary = current["primary_session"]
        
        if self._last_session and self._last_session != primary:
            transition = {
                "from_session": self._last_session,
                "to_session": primary,
                "timestamp": current["timestamp_utc"],
                "new_session_info": current,
            }
            
            self._session_transitions.append(transition)
            
            # Notify callbacks
            for callback in self._transition_callbacks:
                try:
                    callback(transition)
                except Exception as e:
                    logger.error(f"Session transition callback error: {e}")
            
            self._last_session = primary
            return transition
        
        self._last_session = primary
        return None
    
    def get_recent_transitions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent session transitions."""
        return self._session_transitions[-limit:]
    
    def reset_stats(self) -> None:
        """Reset all session statistics."""
        with self._lock:
            for name in self._session_stats:
                self._session_stats[name] = SessionStats(session_name=name)
            self._session_transitions.clear()


# Global session manager instance
session_manager = SessionManager()


def get_session_for_timestamp(timestamp_ms: int) -> str:
    """Convenience function to get session name for timestamp."""
    return session_manager.detect_session(timestamp_ms)


def get_current_session_info() -> Dict[str, Any]:
    """Convenience function to get current session info."""
    return session_manager.get_current_session()
