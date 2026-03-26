"""
Arbitrage Diagnostics Module

Provides detailed explanations for arbitrage detection results.
Designed for research: explains WHY opportunities exist or don't exist.

Design Decisions:
- Diagnostic-first approach for research systems
- Explains thresholds, spreads, latency adjustments
- Tracks historical patterns for context
- No execution logic - purely observational
"""

import time
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict
import statistics

from backend.core.interfaces.normalized_tick import NormalizedTick
from backend.core.session_manager import session_manager, get_current_session_info
from backend.core.arbitrage.arbitrage_engine import ArbitrageOpportunity, ArbitrageType

logger = logging.getLogger(__name__)


@dataclass
class DiagnosticThresholds:
    """
    Thresholds used for arbitrage detection.
    
    Exposed for UI to show users why opportunities are/aren't detected.
    """
    min_profit_pips: float = 0.1
    min_confidence: float = 0.5
    max_latency_risk_ms: float = 100.0
    max_age_ms: int = 5000
    alignment_window_ms: int = 20
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "min_profit_pips": self.min_profit_pips,
            "min_confidence": self.min_confidence,
            "max_latency_risk_ms": self.max_latency_risk_ms,
            "max_age_ms": self.max_age_ms,
            "alignment_window_ms": self.alignment_window_ms,
        }


@dataclass
class SpreadAnalysis:
    """Analysis of spreads across sources."""
    symbol: str
    sources: List[str]
    spreads_by_source: Dict[str, float]
    min_spread_pips: float
    max_spread_pips: float
    avg_spread_pips: float
    spread_variance: float
    best_bid_source: str
    best_ask_source: str
    cross_spread_pips: float  # best_ask - best_bid across sources
    
    @property
    def is_arbitrage_viable(self) -> bool:
        """Negative cross-spread means arbitrage is possible."""
        return self.cross_spread_pips < 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "sources": self.sources,
            "spreads_by_source": {k: round(v, 3) for k, v in self.spreads_by_source.items()},
            "min_spread_pips": round(self.min_spread_pips, 3),
            "max_spread_pips": round(self.max_spread_pips, 3),
            "avg_spread_pips": round(self.avg_spread_pips, 3),
            "spread_variance": round(self.spread_variance, 5),
            "best_bid_source": self.best_bid_source,
            "best_ask_source": self.best_ask_source,
            "cross_spread_pips": round(self.cross_spread_pips, 3),
            "is_arbitrage_viable": self.is_arbitrage_viable,
        }


@dataclass
class LatencyAnalysis:
    """Analysis of latency effects on arbitrage viability."""
    sources: List[str]
    latencies_by_source: Dict[str, float]
    total_round_trip_ms: float
    fastest_source: str
    slowest_source: str
    latency_differential_ms: float
    execution_window_ms: float
    is_latency_viable: bool
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "sources": self.sources,
            "latencies_by_source": {k: round(v, 1) for k, v in self.latencies_by_source.items()},
            "total_round_trip_ms": round(self.total_round_trip_ms, 1),
            "fastest_source": self.fastest_source,
            "slowest_source": self.slowest_source,
            "latency_differential_ms": round(self.latency_differential_ms, 1),
            "execution_window_ms": round(self.execution_window_ms, 1),
            "is_latency_viable": self.is_latency_viable,
        }


@dataclass
class ArbitrageDiagnostic:
    """
    Complete diagnostic for an arbitrage analysis window.
    
    Explains WHY an opportunity was or wasn't detected.
    """
    timestamp_ms: int
    symbol: str
    session: Dict[str, Any]
    thresholds: DiagnosticThresholds
    spread_analysis: SpreadAnalysis
    latency_analysis: LatencyAnalysis
    opportunity_detected: bool
    opportunity: Optional[ArbitrageOpportunity]
    failure_reasons: List[str]
    near_miss_pips: float  # How close to threshold
    recommendations: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp_ms": self.timestamp_ms,
            "symbol": self.symbol,
            "session": self.session,
            "thresholds": self.thresholds.to_dict(),
            "spread_analysis": self.spread_analysis.to_dict(),
            "latency_analysis": self.latency_analysis.to_dict(),
            "opportunity_detected": self.opportunity_detected,
            "opportunity": self.opportunity.to_dict() if self.opportunity else None,
            "failure_reasons": self.failure_reasons,
            "near_miss_pips": round(self.near_miss_pips, 3),
            "recommendations": self.recommendations,
        }


class ArbitrageDiagnosticsEngine:
    """
    Provides detailed diagnostics for arbitrage detection.
    
    Unlike the ArbitrageEngine which detects opportunities,
    this engine EXPLAINS the detection process for research.
    
    Features:
    - Shows why opportunities aren't detected
    - Tracks near-misses
    - Provides historical context
    - Session-aware analysis
    
    Example:
        diagnostics = ArbitrageDiagnosticsEngine(thresholds)
        
        # Analyze current state
        result = diagnostics.analyze(ticks_by_source)
        
        if not result.opportunity_detected:
            print("No opportunity because:")
            for reason in result.failure_reasons:
                print(f"  - {reason}")
    """
    
    def __init__(
        self,
        thresholds: Optional[DiagnosticThresholds] = None,
        source_latencies: Optional[Dict[str, float]] = None,
    ):
        """
        Initialize diagnostics engine.
        
        Args:
            thresholds: Detection thresholds
            source_latencies: Known latencies per source
        """
        self.thresholds = thresholds or DiagnosticThresholds()
        self.source_latencies = source_latencies or {}
        
        # Historical tracking
        self._near_misses: List[Dict[str, Any]] = []
        self._max_near_misses = 100
        
        # Session statistics
        self._session_diagnostics: Dict[str, List[ArbitrageDiagnostic]] = defaultdict(list)
        
        # Analysis count
        self._analyses_performed = 0
        self._opportunities_found = 0
        self._near_miss_count = 0
    
    def analyze(
        self,
        symbol: str,
        ticks_by_source: Dict[str, NormalizedTick],
        source_latencies: Optional[Dict[str, float]] = None,
    ) -> ArbitrageDiagnostic:
        """
        Perform comprehensive arbitrage diagnostic analysis.
        
        Args:
            symbol: Currency pair symbol
            ticks_by_source: Current ticks from each source
            source_latencies: Optional latency overrides
        
        Returns:
            Complete diagnostic result
        """
        self._analyses_performed += 1
        timestamp_ms = int(time.time() * 1000)
        session = get_current_session_info()
        latencies = source_latencies or self.source_latencies
        
        # Perform spread analysis
        spread_analysis = self._analyze_spreads(symbol, ticks_by_source)
        
        # Perform latency analysis
        latency_analysis = self._analyze_latency(list(ticks_by_source.keys()), latencies)
        
        # Check for opportunity
        failure_reasons = []
        opportunity = None
        near_miss_pips = 0.0
        
        # Check 1: Sufficient sources
        if len(ticks_by_source) < 2:
            failure_reasons.append(
                f"Insufficient sources: need 2+, have {len(ticks_by_source)}"
            )
        
        # Check 2: Cross-spread viability
        if spread_analysis.cross_spread_pips >= 0:
            near_miss_pips = spread_analysis.cross_spread_pips + self.thresholds.min_profit_pips
            failure_reasons.append(
                f"Cross-spread is positive ({spread_analysis.cross_spread_pips:.3f} pips). "
                f"Need negative spread for arbitrage. Best bid from {spread_analysis.best_bid_source} "
                f"is lower than best ask from {spread_analysis.best_ask_source}."
            )
        else:
            # Potential opportunity!
            profit_pips = abs(spread_analysis.cross_spread_pips)
            
            # Check 3: Minimum profit threshold
            if profit_pips < self.thresholds.min_profit_pips:
                near_miss_pips = self.thresholds.min_profit_pips - profit_pips
                failure_reasons.append(
                    f"Profit ({profit_pips:.3f} pips) below threshold ({self.thresholds.min_profit_pips} pips). "
                    f"Miss by {near_miss_pips:.3f} pips."
                )
                self._near_miss_count += 1
        
        # Check 4: Latency viability
        if not latency_analysis.is_latency_viable:
            failure_reasons.append(
                f"Latency risk ({latency_analysis.total_round_trip_ms:.1f}ms) exceeds "
                f"threshold ({self.thresholds.max_latency_risk_ms}ms). "
                f"Opportunity may expire before execution."
            )
        
        # Generate recommendations
        recommendations = self._generate_recommendations(
            spread_analysis, latency_analysis, session, failure_reasons
        )
        
        # Determine if opportunity detected
        opportunity_detected = len(failure_reasons) == 0 and spread_analysis.is_arbitrage_viable
        
        if opportunity_detected:
            self._opportunities_found += 1
            # Create opportunity object
            opportunity = ArbitrageOpportunity(
                type=ArbitrageType.CROSS_SOURCE,
                symbols=[symbol],
                sources=list(ticks_by_source.keys()),
                buy_source=spread_analysis.best_ask_source,
                sell_source=spread_analysis.best_bid_source,
                buy_price=ticks_by_source[spread_analysis.best_ask_source].ask,
                sell_price=ticks_by_source[spread_analysis.best_bid_source].bid,
                estimated_profit_pips=abs(spread_analysis.cross_spread_pips),
                estimated_profit_pct=abs(spread_analysis.cross_spread_pips) * 0.0001,
                latency_risk_ms=latency_analysis.total_round_trip_ms,
                confidence_score=self._calculate_confidence(
                    spread_analysis, latency_analysis, session
                ),
                session=session["primary_session"],
                timestamp_ms=timestamp_ms,
                window_size_ms=self.thresholds.alignment_window_ms,
            )
        
        diagnostic = ArbitrageDiagnostic(
            timestamp_ms=timestamp_ms,
            symbol=symbol,
            session=session,
            thresholds=self.thresholds,
            spread_analysis=spread_analysis,
            latency_analysis=latency_analysis,
            opportunity_detected=opportunity_detected,
            opportunity=opportunity,
            failure_reasons=failure_reasons,
            near_miss_pips=near_miss_pips,
            recommendations=recommendations,
        )
        
        # Track for historical analysis
        self._session_diagnostics[session["primary_session"]].append(diagnostic)
        
        return diagnostic
    
    def _analyze_spreads(
        self,
        symbol: str,
        ticks_by_source: Dict[str, NormalizedTick],
    ) -> SpreadAnalysis:
        """Analyze spreads across sources."""
        pip_value = 0.01 if "JPY" in symbol else 0.0001
        
        spreads = {}
        bids = {}
        asks = {}
        
        for source_id, tick in ticks_by_source.items():
            spreads[source_id] = tick.spread / pip_value
            bids[source_id] = tick.bid
            asks[source_id] = tick.ask
        
        spread_values = list(spreads.values())
        
        # Find best prices across sources
        best_bid_source = max(bids.keys(), key=lambda k: bids[k])
        best_ask_source = min(asks.keys(), key=lambda k: asks[k])
        
        # Cross-source spread (negative = arbitrage opportunity)
        cross_spread = asks[best_ask_source] - bids[best_bid_source]
        cross_spread_pips = cross_spread / pip_value
        
        return SpreadAnalysis(
            symbol=symbol,
            sources=list(ticks_by_source.keys()),
            spreads_by_source=spreads,
            min_spread_pips=min(spread_values) if spread_values else 0,
            max_spread_pips=max(spread_values) if spread_values else 0,
            avg_spread_pips=statistics.mean(spread_values) if spread_values else 0,
            spread_variance=statistics.variance(spread_values) if len(spread_values) > 1 else 0,
            best_bid_source=best_bid_source,
            best_ask_source=best_ask_source,
            cross_spread_pips=cross_spread_pips,
        )
    
    def _analyze_latency(
        self,
        sources: List[str],
        latencies: Dict[str, float],
    ) -> LatencyAnalysis:
        """Analyze latency effects."""
        # Get latencies for each source (default to 50ms if unknown)
        source_latencies = {s: latencies.get(s, 50.0) for s in sources}
        
        if not source_latencies:
            return LatencyAnalysis(
                sources=sources,
                latencies_by_source={},
                total_round_trip_ms=0,
                fastest_source="",
                slowest_source="",
                latency_differential_ms=0,
                execution_window_ms=0,
                is_latency_viable=True,
            )
        
        fastest = min(source_latencies.keys(), key=lambda k: source_latencies[k])
        slowest = max(source_latencies.keys(), key=lambda k: source_latencies[k])
        
        # Total round-trip = time to get quote + time to execute on both sides
        total_rt = source_latencies[fastest] + source_latencies[slowest]
        
        # Execution window = difference between sources
        differential = source_latencies[slowest] - source_latencies[fastest]
        
        # Viable if total RT is within threshold
        is_viable = total_rt <= self.thresholds.max_latency_risk_ms
        
        return LatencyAnalysis(
            sources=sources,
            latencies_by_source=source_latencies,
            total_round_trip_ms=total_rt,
            fastest_source=fastest,
            slowest_source=slowest,
            latency_differential_ms=differential,
            execution_window_ms=self.thresholds.alignment_window_ms + differential,
            is_latency_viable=is_viable,
        )
    
    def _calculate_confidence(
        self,
        spread: SpreadAnalysis,
        latency: LatencyAnalysis,
        session: Dict[str, Any],
    ) -> float:
        """Calculate confidence score for opportunity."""
        confidence = 1.0
        
        # Reduce confidence for high latency
        if latency.total_round_trip_ms > 50:
            confidence *= 0.9
        if latency.total_round_trip_ms > 75:
            confidence *= 0.85
        
        # Reduce confidence for high spread variance
        if spread.spread_variance > 0.5:
            confidence *= 0.9
        
        # Boost confidence for overlap sessions (higher liquidity)
        if session.get("is_overlap"):
            confidence *= 1.1
        
        # Reduce confidence for low liquidity sessions
        if session.get("liquidity") == "moderate":
            confidence *= 0.95
        
        return min(1.0, max(0.0, confidence))
    
    def _generate_recommendations(
        self,
        spread: SpreadAnalysis,
        latency: LatencyAnalysis,
        session: Dict[str, Any],
        failures: List[str],
    ) -> List[str]:
        """Generate actionable recommendations."""
        recommendations = []
        
        if spread.cross_spread_pips > 0 and spread.cross_spread_pips < 0.5:
            recommendations.append(
                "Cross-spread is close to zero. Consider monitoring during "
                "higher volatility periods for potential opportunities."
            )
        
        if latency.latency_differential_ms > 30:
            recommendations.append(
                f"Large latency gap ({latency.latency_differential_ms:.0f}ms) between sources. "
                f"The faster source ({latency.fastest_source}) may provide stale-quote opportunities."
            )
        
        if not session.get("is_overlap") and session.get("liquidity") != "high":
            recommendations.append(
                f"Current session ({session.get('display_name')}) has {session.get('liquidity')} liquidity. "
                f"Consider London/NY overlap for more opportunities."
            )
        
        if spread.max_spread_pips - spread.min_spread_pips > 1.0:
            recommendations.append(
                f"Large spread variance ({spread.max_spread_pips - spread.min_spread_pips:.2f} pips) "
                f"across sources suggests pricing inefficiency worth monitoring."
            )
        
        if not failures:
            recommendations.append(
                "Opportunity detected. Note: This is a research system. "
                "Actual execution would require additional latency and slippage considerations."
            )
        
        return recommendations
    
    def get_stats(self) -> Dict[str, Any]:
        """Get diagnostic statistics."""
        return {
            "analyses_performed": self._analyses_performed,
            "opportunities_found": self._opportunities_found,
            "near_miss_count": self._near_miss_count,
            "detection_rate_pct": (
                self._opportunities_found / self._analyses_performed * 100
                if self._analyses_performed > 0 else 0
            ),
            "sessions_analyzed": list(self._session_diagnostics.keys()),
        }
    
    def get_current_state_explanation(
        self,
        symbol: str,
        ticks_by_source: Dict[str, NormalizedTick],
    ) -> Dict[str, Any]:
        """
        Get human-readable explanation of current state.
        
        For UI display when no opportunity is detected.
        """
        diagnostic = self.analyze(symbol, ticks_by_source)
        
        # Build explanation
        if diagnostic.opportunity_detected:
            headline = "✅ Arbitrage Opportunity Detected"
            detail = (
                f"Buy from {diagnostic.spread_analysis.best_ask_source}, "
                f"sell to {diagnostic.spread_analysis.best_bid_source}. "
                f"Estimated profit: {abs(diagnostic.spread_analysis.cross_spread_pips):.2f} pips."
            )
        else:
            headline = "ℹ️ No Arbitrage Opportunity"
            if diagnostic.near_miss_pips < 0.5 and diagnostic.near_miss_pips > 0:
                detail = f"Near miss! Only {diagnostic.near_miss_pips:.2f} pips from threshold."
            else:
                detail = diagnostic.failure_reasons[0] if diagnostic.failure_reasons else "Prices are aligned across sources."
        
        return {
            "headline": headline,
            "detail": detail,
            "session": diagnostic.session.get("display_name", "Unknown"),
            "sources_active": len(diagnostic.spread_analysis.sources),
            "cross_spread_pips": diagnostic.spread_analysis.cross_spread_pips,
            "threshold_pips": self.thresholds.min_profit_pips,
            "is_near_miss": 0 < diagnostic.near_miss_pips < 0.5,
            "failure_reasons": diagnostic.failure_reasons,
            "recommendations": diagnostic.recommendations,
            "latency_info": {
                "total_ms": diagnostic.latency_analysis.total_round_trip_ms,
                "is_viable": diagnostic.latency_analysis.is_latency_viable,
            },
        }


# NOTE: Do not instantiate a global singleton here.
# Create instances explicitly in main.py and inject where needed.
# This follows the dependency injection pattern for testability.
