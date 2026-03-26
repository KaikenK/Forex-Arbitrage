"""
Experimental Metrics Collector

Aggregates runtime statistics for research analysis and experimental output.
Tracks opportunity counts, durations, persistence distributions, and
session-specific metrics for academic evaluation.

Design Principles:
    - Thread-safe with RLock for concurrent access
    - Accumulates statistics without storing raw data (memory-efficient)
    - Session-aware aggregation for comparative analysis
    - Exposes a single get_summary() method for API consumption
"""

import time
import logging
from typing import Dict, List, Optional, Any
from collections import defaultdict
from threading import RLock
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SessionMetrics:
    """Accumulated metrics for a single trading session."""
    tick_count: int = 0
    opportunity_count: int = 0
    total_duration_ms: int = 0
    total_profit_pips: float = 0.0
    ephemeral_count: int = 0
    flickering_count: int = 0
    persistent_count: int = 0
    spread_sum: float = 0.0
    spread_count: int = 0

    @property
    def avg_duration_ms(self) -> float:
        """Average opportunity duration in milliseconds."""
        return self.total_duration_ms / max(1, self.opportunity_count)

    @property
    def avg_spread_pips(self) -> float:
        """Average spread in pips for this session."""
        return self.spread_sum / max(1, self.spread_count)

    @property
    def avg_profit_pips(self) -> float:
        """Average profit per opportunity in pips."""
        return self.total_profit_pips / max(1, self.opportunity_count)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "tick_count": self.tick_count,
            "opportunity_count": self.opportunity_count,
            "avg_duration_ms": round(self.avg_duration_ms, 1),
            "avg_profit_pips": round(self.avg_profit_pips, 3),
            "avg_spread_pips": round(self.avg_spread_pips, 3),
            "persistence_distribution": {
                "ephemeral": self.ephemeral_count,
                "flickering": self.flickering_count,
                "persistent": self.persistent_count,
            },
        }


class MetricsCollector:
    """
    Collects and aggregates experimental metrics for research output.

    Thread-safe. Designed to be called from the arbitrage pipeline
    callbacks without blocking the main event loop.

    Usage:
        collector = MetricsCollector()

        # Record from pipeline callbacks
        collector.record_tick("LONDON", spread_pips=1.2)
        collector.record_opportunity(
            session="LONDON",
            duration_ms=150,
            profit_pips=0.3,
            persistence_class="flickering",
        )

        # Get summary for API
        summary = collector.get_summary()
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._start_time_ms = int(time.time() * 1000)

        # Global counters
        self._total_ticks = 0
        self._total_opportunities = 0
        self._total_duration_ms = 0
        self._total_profit_pips = 0.0

        # Persistence distribution (global)
        self._ephemeral_count = 0
        self._flickering_count = 0
        self._persistent_count = 0

        # Session-specific metrics
        self._sessions: Dict[str, SessionMetrics] = defaultdict(SessionMetrics)

        logger.info("[MetricsCollector] Initialized")

    def record_tick(
        self,
        session: str,
        spread_pips: float,
    ) -> None:
        """
        Record a tick for session-level spread tracking.

        Args:
            session: Trading session name (e.g., "LONDON", "TOKYO")
            spread_pips: Bid-ask spread in pips
        """
        with self._lock:
            self._total_ticks += 1
            s = self._sessions[session]
            s.tick_count += 1
            s.spread_sum += spread_pips
            s.spread_count += 1

    def record_opportunity(
        self,
        session: str,
        duration_ms: int,
        profit_pips: float,
        persistence_class: str,
    ) -> None:
        """
        Record a detected arbitrage opportunity.

        Args:
            session: Trading session when detected
            duration_ms: Cumulative duration of the opportunity
            profit_pips: Estimated profit in pips
            persistence_class: One of "ephemeral", "flickering", "persistent"
        """
        with self._lock:
            self._total_opportunities += 1
            self._total_duration_ms += duration_ms
            self._total_profit_pips += profit_pips

            # Update persistence distribution
            pc = persistence_class.lower()
            if pc == "ephemeral":
                self._ephemeral_count += 1
            elif pc == "flickering":
                self._flickering_count += 1
            elif pc == "persistent":
                self._persistent_count += 1

            # Update session metrics
            s = self._sessions[session]
            s.opportunity_count += 1
            s.total_duration_ms += duration_ms
            s.total_profit_pips += profit_pips
            if pc == "ephemeral":
                s.ephemeral_count += 1
            elif pc == "flickering":
                s.flickering_count += 1
            elif pc == "persistent":
                s.persistent_count += 1

    def get_summary(self) -> Dict[str, Any]:
        """
        Get complete metrics summary for API consumption.

        Returns:
            Dictionary with global and per-session experimental metrics.
        """
        with self._lock:
            uptime_ms = int(time.time() * 1000) - self._start_time_ms
            total_opp = max(1, self._total_opportunities)

            return {
                "uptime_ms": uptime_ms,
                "uptime_seconds": round(uptime_ms / 1000, 1),
                "total_ticks_processed": self._total_ticks,
                "total_opportunities_detected": self._total_opportunities,
                "avg_opportunity_duration_ms": round(
                    self._total_duration_ms / total_opp, 1
                ),
                "avg_profit_pips": round(
                    self._total_profit_pips / total_opp, 3
                ),
                "persistence_distribution": {
                    "ephemeral": self._ephemeral_count,
                    "flickering": self._flickering_count,
                    "persistent": self._persistent_count,
                },
                "persistence_percentages": {
                    "ephemeral_pct": round(
                        self._ephemeral_count / total_opp * 100, 1
                    ),
                    "flickering_pct": round(
                        self._flickering_count / total_opp * 100, 1
                    ),
                    "persistent_pct": round(
                        self._persistent_count / total_opp * 100, 1
                    ),
                },
                "session_metrics": {
                    name: metrics.to_dict()
                    for name, metrics in self._sessions.items()
                },
            }

    def reset(self) -> None:
        """Reset all metrics (for new research sessions)."""
        with self._lock:
            self._start_time_ms = int(time.time() * 1000)
            self._total_ticks = 0
            self._total_opportunities = 0
            self._total_duration_ms = 0
            self._total_profit_pips = 0.0
            self._ephemeral_count = 0
            self._flickering_count = 0
            self._persistent_count = 0
            self._sessions.clear()
            logger.info("[MetricsCollector] Reset")
