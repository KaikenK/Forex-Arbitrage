"""
Arbitrage Detection Engine

Detects various types of arbitrage opportunities from aligned tick windows:
1. Cross-source arbitrage: Same symbol, different prices across sources
2. Triangular arbitrage: Profit from currency triangles (e.g., USD→EUR→GBP→USD)
3. Session-based inefficiencies: Spread widening, delayed updates, volatility spikes

Design Decisions:
- Stateless detection for each window (no memory between windows)
- Configurable thresholds for opportunity detection
- Confidence scoring based on multiple factors
- Latency risk estimation for execution feasibility
"""

import logging
import time
from typing import Dict, List, Optional, Set, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

from backend.core.arbitrage.tick_aligner import AlignedTickWindow
from backend.core.interfaces.normalized_tick import NormalizedTick

logger = logging.getLogger(__name__)


class ArbitrageType(str, Enum):
    """Types of arbitrage opportunities."""
    CROSS_SOURCE = "cross_source"
    TRIANGULAR = "triangular"
    SESSION_INEFFICIENCY = "session_inefficiency"
    LATENCY_ARBITRAGE = "latency_arbitrage"


@dataclass
class ArbitrageOpportunity:
    """
    Represents a detected arbitrage opportunity.
    
    Contains all information needed for evaluation and potential execution.
    
    Attributes:
        type: Type of arbitrage opportunity
        symbols: Currency pair(s) involved
        sources: Data sources involved in the opportunity
        buy_source: Source to buy from (lower ask)
        sell_source: Source to sell to (higher bid)
        buy_price: Price to buy at
        sell_price: Price to sell at
        estimated_profit_pips: Estimated profit in pips
        estimated_profit_pct: Estimated profit as percentage
        latency_risk_ms: Estimated latency risk in milliseconds
        confidence_score: Confidence score (0.0 to 1.0)
        session: Trading session when opportunity was detected
        timestamp_ms: Detection timestamp
        window_size_ms: Size of the alignment window
        details: Additional opportunity-specific details
    """
    type: ArbitrageType
    symbols: List[str]
    sources: List[str]
    buy_source: str
    sell_source: str
    buy_price: float
    sell_price: float
    estimated_profit_pips: float
    estimated_profit_pct: float
    latency_risk_ms: float
    confidence_score: float
    session: str
    timestamp_ms: int
    window_size_ms: int
    details: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def id(self) -> str:
        """Generate a unique ID for this opportunity."""
        return f"{self.type.value}_{'-'.join(self.symbols)}_{self.timestamp_ms}"
    
    @property
    def is_profitable(self) -> bool:
        """Check if opportunity is profitable after spread."""
        return self.estimated_profit_pips > 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "type": self.type.value,
            "symbols": self.symbols,
            "sources": self.sources,
            "buy_source": self.buy_source,
            "sell_source": self.sell_source,
            "buy_price": self.buy_price,
            "sell_price": self.sell_price,
            "estimated_profit_pips": round(self.estimated_profit_pips, 2),
            "estimated_profit_pct": round(self.estimated_profit_pct, 6),
            "latency_risk_ms": round(self.latency_risk_ms, 1),
            "confidence_score": round(self.confidence_score, 3),
            "session": self.session,
            "timestamp_ms": self.timestamp_ms,
            "window_size_ms": self.window_size_ms,
            "details": self.details,
        }


@dataclass
class ArbitrageConfig:
    """
    Configuration for arbitrage detection.
    
    Attributes:
        min_profit_pips: Minimum profit in pips to consider an opportunity
        min_confidence: Minimum confidence score (0.0 to 1.0)
        max_latency_risk_ms: Maximum acceptable latency risk
        enable_cross_source: Enable cross-source arbitrage detection
        enable_triangular: Enable triangular arbitrage detection
        enable_session_analysis: Enable session inefficiency detection
        source_latencies: Known latencies for each source (ms)
        source_reliabilities: Reliability scores for each source (0.0 to 1.0)
    """
    min_profit_pips: float = 0.1  # 0.1 pip minimum
    min_confidence: float = 0.5
    max_latency_risk_ms: float = 100.0
    enable_cross_source: bool = True
    enable_triangular: bool = True
    enable_session_analysis: bool = True
    source_latencies: Dict[str, float] = field(default_factory=dict)
    source_reliabilities: Dict[str, float] = field(default_factory=dict)


class ArbitrageEngine:
    """
    Detects arbitrage opportunities from aligned tick windows.
    
    The engine analyzes each aligned window for various types of
    arbitrage opportunities and emits structured opportunity objects.
    
    Example:
        engine = ArbitrageEngine(config=ArbitrageConfig(
            min_profit_pips=0.5,
            source_latencies={"mt5": 50, "bloomberg": 100}
        ))
        
        # Process a window
        opportunities = engine.detect(aligned_window)
        
        for opp in opportunities:
            print(f"Found {opp.type}: {opp.estimated_profit_pips} pips")
    """
    
    # Standard triangular arbitrage paths
    # Each path is (base, quote1, quote2) forming a triangle
    TRIANGULAR_PATHS = [
        # Major triangles
        ("USD", "EUR", "GBP"),  # USD → EUR → GBP → USD
        ("USD", "EUR", "JPY"),  # USD → EUR → JPY → USD
        ("USD", "GBP", "JPY"),  # USD → GBP → JPY → USD
        ("USD", "EUR", "CHF"),  # USD → EUR → CHF → USD
        ("USD", "AUD", "JPY"),  # USD → AUD → JPY → USD
        # Cross triangles
        ("EUR", "GBP", "JPY"),  # EUR → GBP → JPY → EUR
        ("EUR", "CHF", "GBP"),  # EUR → CHF → GBP → EUR
    ]
    
    def __init__(self, config: Optional[ArbitrageConfig] = None):
        """
        Initialize the arbitrage engine.
        
        Args:
            config: Arbitrage detection configuration
        """
        self.config = config or ArbitrageConfig()
        
        # Detection stats
        self._windows_analyzed = 0
        self._opportunities_detected = 0
        self._opportunities_by_type: Dict[ArbitrageType, int] = {
            t: 0 for t in ArbitrageType
        }
        
        # Session spread tracking for inefficiency detection
        self._session_spreads: Dict[str, Dict[str, List[float]]] = {}  # {symbol: {session: [spreads]}}
    
    def detect(self, window: AlignedTickWindow) -> List[ArbitrageOpportunity]:
        """
        Detect arbitrage opportunities in an aligned window.
        
        Args:
            window: Aligned tick window with prices from multiple sources
        
        Returns:
            List of detected arbitrage opportunities
        """
        opportunities: List[ArbitrageOpportunity] = []
        self._windows_analyzed += 1
        
        # Need at least 2 sources for arbitrage
        if not window.has_multiple_sources():
            return opportunities
        
        # Get best tick from each source
        ticks_by_source = window.get_best_tick_per_source()
        
        # Cross-source arbitrage
        if self.config.enable_cross_source:
            cross_opps = self._detect_cross_source(window, ticks_by_source)
            opportunities.extend(cross_opps)
        
        # Session inefficiency
        if self.config.enable_session_analysis:
            session_opps = self._detect_session_inefficiency(window, ticks_by_source)
            opportunities.extend(session_opps)
        
        # Filter by minimum confidence and profit
        filtered = [
            opp for opp in opportunities
            if opp.confidence_score >= self.config.min_confidence
            and opp.estimated_profit_pips >= self.config.min_profit_pips
            and opp.latency_risk_ms <= self.config.max_latency_risk_ms
        ]
        
        self._opportunities_detected += len(filtered)
        for opp in filtered:
            self._opportunities_by_type[opp.type] += 1
        
        return filtered
    
    def detect_triangular(
        self, 
        windows_by_symbol: Dict[str, AlignedTickWindow]
    ) -> List[ArbitrageOpportunity]:
        """
        Detect triangular arbitrage opportunities.
        
        Requires windows for multiple symbols to be analyzed together.
        
        Args:
            windows_by_symbol: Dict mapping symbol to aligned window
        
        Returns:
            List of triangular arbitrage opportunities
        """
        if not self.config.enable_triangular:
            return []
        
        opportunities: List[ArbitrageOpportunity] = []
        
        for base, quote1, quote2 in self.TRIANGULAR_PATHS:
            opp = self._check_triangle(base, quote1, quote2, windows_by_symbol)
            if opp and opp.estimated_profit_pips >= self.config.min_profit_pips:
                opportunities.append(opp)
        
        return opportunities
    
    def _detect_cross_source(
        self,
        window: AlignedTickWindow,
        ticks_by_source: Dict[str, NormalizedTick],
    ) -> List[ArbitrageOpportunity]:
        """
        Detect cross-source arbitrage.
        
        Looks for cases where one source's bid > another source's ask,
        indicating a risk-free profit opportunity.
        
        Args:
            window: The aligned window
            ticks_by_source: Best tick from each source
        
        Returns:
            List of cross-source opportunities
        """
        opportunities = []
        sources = list(ticks_by_source.keys())
        
        # Compare all source pairs
        for i, source_a in enumerate(sources):
            for source_b in sources[i + 1:]:
                tick_a = ticks_by_source[source_a]
                tick_b = ticks_by_source[source_b]
                
                # Check A.bid > B.ask (buy from B, sell to A)
                if tick_a.bid > tick_b.ask:
                    opp = self._create_cross_source_opportunity(
                        window=window,
                        buy_source=source_b,
                        sell_source=source_a,
                        buy_tick=tick_b,
                        sell_tick=tick_a,
                    )
                    opportunities.append(opp)
                
                # Check B.bid > A.ask (buy from A, sell to B)
                elif tick_b.bid > tick_a.ask:
                    opp = self._create_cross_source_opportunity(
                        window=window,
                        buy_source=source_a,
                        sell_source=source_b,
                        buy_tick=tick_a,
                        sell_tick=tick_b,
                    )
                    opportunities.append(opp)
                
                # Check for near-arbitrage (small positive spread)
                else:
                    # Calculate potential profit if we could beat the spread
                    spread_a = tick_a.spread
                    spread_b = tick_b.spread
                    mid_diff = abs(tick_a.mid - tick_b.mid)
                    
                    # If mid prices differ significantly, there might be latency arb
                    pip_value = 0.01 if "JPY" in window.symbol or "INR" in window.symbol else 0.0001
                    mid_diff_pips = mid_diff / pip_value
                    
                    if mid_diff_pips >= 0.5:  # At least 0.5 pip difference
                        opp = self._create_latency_opportunity(
                            window=window,
                            source_a=source_a,
                            source_b=source_b,
                            tick_a=tick_a,
                            tick_b=tick_b,
                            mid_diff_pips=mid_diff_pips,
                        )
                        opportunities.append(opp)
        
        return opportunities
    
    def _create_cross_source_opportunity(
        self,
        window: AlignedTickWindow,
        buy_source: str,
        sell_source: str,
        buy_tick: NormalizedTick,
        sell_tick: NormalizedTick,
    ) -> ArbitrageOpportunity:
        """Create a cross-source arbitrage opportunity."""
        # Calculate profit
        profit = sell_tick.bid - buy_tick.ask
        pip_value = 0.01 if "JPY" in window.symbol or "INR" in window.symbol else 0.0001
        profit_pips = profit / pip_value
        profit_pct = profit / buy_tick.ask
        
        # Estimate latency risk
        buy_latency = self.config.source_latencies.get(buy_source, 50.0)
        sell_latency = self.config.source_latencies.get(sell_source, 50.0)
        window_size_ms = window.window_end_ms - window.window_start_ms
        total_latency = buy_latency + sell_latency + window_size_ms
        
        # Calculate confidence
        confidence = self._calculate_confidence(
            profit_pips=profit_pips,
            latency_ms=total_latency,
            sources=[buy_source, sell_source],
            window=window,
        )
        
        return ArbitrageOpportunity(
            type=ArbitrageType.CROSS_SOURCE,
            symbols=[window.symbol],
            sources=[buy_source, sell_source],
            buy_source=buy_source,
            sell_source=sell_source,
            buy_price=buy_tick.ask,
            sell_price=sell_tick.bid,
            estimated_profit_pips=profit_pips,
            estimated_profit_pct=profit_pct,
            latency_risk_ms=total_latency,
            confidence_score=confidence,
            session=buy_tick.session,
            timestamp_ms=int(time.time() * 1000),
            window_size_ms=window.window_end_ms - window.window_start_ms,
            details={
                "buy_bid": buy_tick.bid,
                "buy_ask": buy_tick.ask,
                "buy_spread": buy_tick.spread,
                "sell_bid": sell_tick.bid,
                "sell_ask": sell_tick.ask,
                "sell_spread": sell_tick.spread,
            },
        )
    
    def _create_latency_opportunity(
        self,
        window: AlignedTickWindow,
        source_a: str,
        source_b: str,
        tick_a: NormalizedTick,
        tick_b: NormalizedTick,
        mid_diff_pips: float,
    ) -> ArbitrageOpportunity:
        """Create a latency arbitrage opportunity."""
        # Determine direction based on which source has higher mid
        if tick_a.mid > tick_b.mid:
            # A is ahead, B is lagging - buy from B expecting price to rise
            buy_source, sell_source = source_b, source_a
            buy_tick, sell_tick = tick_b, tick_a
        else:
            buy_source, sell_source = source_a, source_b
            buy_tick, sell_tick = tick_a, tick_b
        
        pip_value = 0.01 if "JPY" in window.symbol or "INR" in window.symbol else 0.0001
        
        # Estimated profit is the mid difference minus likely spread costs
        avg_spread = (tick_a.spread + tick_b.spread) / 2
        net_profit = (mid_diff_pips * pip_value) - avg_spread
        profit_pips = net_profit / pip_value
        
        latency_a = self.config.source_latencies.get(source_a, 50.0)
        latency_b = self.config.source_latencies.get(source_b, 50.0)
        
        confidence = self._calculate_confidence(
            profit_pips=profit_pips,
            latency_ms=max(latency_a, latency_b),
            sources=[source_a, source_b],
            window=window,
        ) * 0.7  # Reduce confidence for latency arb (riskier)
        
        return ArbitrageOpportunity(
            type=ArbitrageType.LATENCY_ARBITRAGE,
            symbols=[window.symbol],
            sources=[source_a, source_b],
            buy_source=buy_source,
            sell_source=sell_source,
            buy_price=buy_tick.ask,
            sell_price=sell_tick.bid,
            estimated_profit_pips=profit_pips,
            estimated_profit_pct=profit_pips * pip_value / buy_tick.ask,
            latency_risk_ms=max(latency_a, latency_b),
            confidence_score=confidence,
            session=tick_a.session,
            timestamp_ms=int(time.time() * 1000),
            window_size_ms=window.window_end_ms - window.window_start_ms,
            details={
                "mid_difference_pips": mid_diff_pips,
                "lagging_source": buy_source,
                "leading_source": sell_source,
                "latency_difference_ms": abs(latency_a - latency_b),
            },
        )
    
    def _detect_session_inefficiency(
        self,
        window: AlignedTickWindow,
        ticks_by_source: Dict[str, NormalizedTick],
    ) -> List[ArbitrageOpportunity]:
        """
        Detect session-based trading inefficiencies.
        
        Looks for:
        - Spread widening during session transitions
        - Delayed price updates from slower sources
        - Unusual volatility during low-liquidity periods
        
        Args:
            window: The aligned window
            ticks_by_source: Best tick from each source
        
        Returns:
            List of session inefficiency opportunities
        """
        opportunities = []
        
        if not ticks_by_source:
            return opportunities
        
        # Get any tick to determine session
        sample_tick = next(iter(ticks_by_source.values()))
        session = sample_tick.session
        symbol = window.symbol
        
        # Calculate average spread across sources
        spreads = [t.spread for t in ticks_by_source.values()]
        avg_spread = sum(spreads) / len(spreads)
        max_spread = max(spreads)
        min_spread = min(spreads)
        spread_variance = max_spread - min_spread
        
        pip_value = 0.01 if "JPY" in symbol or "INR" in symbol else 0.0001
        spread_variance_pips = spread_variance / pip_value
        
        # Track spreads for this session
        if symbol not in self._session_spreads:
            self._session_spreads[symbol] = {}
        if session not in self._session_spreads[symbol]:
            self._session_spreads[symbol][session] = []
        
        self._session_spreads[symbol][session].append(avg_spread)
        
        # Keep only last 100 spreads per session
        if len(self._session_spreads[symbol][session]) > 100:
            self._session_spreads[symbol][session] = self._session_spreads[symbol][session][-100:]
        
        # Detect spread widening (current spread >> historical)
        historical_spreads = self._session_spreads[symbol][session]
        if len(historical_spreads) >= 10:
            historical_avg = sum(historical_spreads[:-1]) / (len(historical_spreads) - 1)
            
            if avg_spread > historical_avg * 1.5:  # 50% wider than average
                widening_pips = (avg_spread - historical_avg) / pip_value
                
                # Find the source with widest spread (potential arb target)
                widest_source = max(ticks_by_source.keys(), 
                                   key=lambda s: ticks_by_source[s].spread)
                narrowest_source = min(ticks_by_source.keys(),
                                      key=lambda s: ticks_by_source[s].spread)
                
                if widest_source != narrowest_source:
                    opp = ArbitrageOpportunity(
                        type=ArbitrageType.SESSION_INEFFICIENCY,
                        symbols=[symbol],
                        sources=[narrowest_source, widest_source],
                        buy_source=narrowest_source,
                        sell_source=widest_source,
                        buy_price=ticks_by_source[narrowest_source].ask,
                        sell_price=ticks_by_source[widest_source].bid,
                        estimated_profit_pips=spread_variance_pips * 0.5,  # Conservative
                        estimated_profit_pct=spread_variance / sample_tick.mid,
                        latency_risk_ms=(window.window_end_ms - window.window_start_ms) * 2,
                        confidence_score=min(0.8, widening_pips / 5.0),  # Higher widening = higher confidence
                        session=session,
                        timestamp_ms=int(time.time() * 1000),
                        window_size_ms=window.window_end_ms - window.window_start_ms,
                        details={
                            "inefficiency_type": "spread_widening",
                            "current_spread": avg_spread,
                            "historical_spread": historical_avg,
                            "widening_pips": widening_pips,
                            "widest_source": widest_source,
                            "narrowest_source": narrowest_source,
                        },
                    )
                    opportunities.append(opp)
        
        return opportunities
    
    def _check_triangle(
        self,
        base: str,
        quote1: str,
        quote2: str,
        windows_by_symbol: Dict[str, AlignedTickWindow],
    ) -> Optional[ArbitrageOpportunity]:
        """
        Check for triangular arbitrage in a currency triangle.
        
        For triangle BASE/QUOTE1/QUOTE2:
        Path 1: BASE → QUOTE1 → QUOTE2 → BASE
        Path 2: BASE → QUOTE2 → QUOTE1 → BASE (reverse)
        
        Args:
            base: Base currency
            quote1: First quote currency
            quote2: Second quote currency
            windows_by_symbol: Windows for relevant pairs
        
        Returns:
            ArbitrageOpportunity if profitable triangle found
        """
        # Construct pair names (try both orderings)
        def find_pair(c1: str, c2: str) -> Tuple[Optional[str], bool]:
            """Find pair and whether it's inverted."""
            direct = f"{c1}{c2}"
            inverse = f"{c2}{c1}"
            if direct in windows_by_symbol:
                return direct, False
            elif inverse in windows_by_symbol:
                return inverse, True
            return None, False
        
        pair1_name, pair1_inv = find_pair(base, quote1)
        pair2_name, pair2_inv = find_pair(quote1, quote2)
        pair3_name, pair3_inv = find_pair(quote2, base)
        
        # Need all three pairs
        if not all([pair1_name, pair2_name, pair3_name]):
            return None
        
        # Get best ticks from each window
        def get_mid(pair_name: str) -> Optional[float]:
            window = windows_by_symbol.get(pair_name)
            if not window or not window.ticks_by_source:
                return None
            ticks = window.get_best_tick_per_source()
            if not ticks:
                return None
            # Use average mid across sources
            mids = [t.mid for t in ticks.values()]
            return sum(mids) / len(mids)
        
        mid1 = get_mid(pair1_name)
        mid2 = get_mid(pair2_name)
        mid3 = get_mid(pair3_name)
        
        if not all([mid1, mid2, mid3]):
            return None
        
        # Calculate triangle product
        # Adjust for pair inversion
        rate1 = mid1 if not pair1_inv else 1/mid1
        rate2 = mid2 if not pair2_inv else 1/mid2
        rate3 = mid3 if not pair3_inv else 1/mid3
        
        # Forward path: start with 1 BASE, end with X BASE
        forward_result = rate1 * rate2 * rate3
        
        # Profit is deviation from 1.0
        profit_pct = abs(forward_result - 1.0)
        
        # Convert to approximate pips (rough estimate)
        profit_pips = profit_pct * 10000
        
        if profit_pips < self.config.min_profit_pips:
            return None
        
        # Determine direction
        if forward_result > 1.0:
            direction = "forward"
            estimated_profit = forward_result - 1.0
        else:
            direction = "reverse"
            estimated_profit = 1.0 - forward_result
        
        symbols = [pair1_name, pair2_name, pair3_name]
        sources = list(set(
            s for name in symbols 
            for s in windows_by_symbol[name].ticks_by_source.keys()
        ))
        
        window = windows_by_symbol[pair1_name]  # Use first window for metadata
        
        return ArbitrageOpportunity(
            type=ArbitrageType.TRIANGULAR,
            symbols=symbols,
            sources=sources,
            buy_source=sources[0] if sources else "unknown",
            sell_source=sources[-1] if sources else "unknown",
            buy_price=mid1,
            sell_price=mid3,
            estimated_profit_pips=profit_pips,
            estimated_profit_pct=estimated_profit,
            latency_risk_ms=(window.window_end_ms - window.window_start_ms) * 3,  # Need to execute 3 trades
            confidence_score=min(0.9, profit_pips / 10.0),
            session=list(window.get_best_tick_per_source().values())[0].session if window.get_best_tick_per_source() else "unknown",
            timestamp_ms=int(time.time() * 1000),
            window_size_ms=window.window_end_ms - window.window_start_ms,
            details={
                "triangle": f"{base}→{quote1}→{quote2}→{base}",
                "direction": direction,
                "rate1": rate1,
                "rate2": rate2,
                "rate3": rate3,
                "product": forward_result,
                "pair1_inverted": pair1_inv,
                "pair2_inverted": pair2_inv,
                "pair3_inverted": pair3_inv,
            },
        )
    
    def _calculate_confidence(
        self,
        profit_pips: float,
        latency_ms: float,
        sources: List[str],
        window: AlignedTickWindow,
    ) -> float:
        """
        Calculate confidence score for an opportunity.
        
        Factors:
        - Higher profit = higher confidence
        - Lower latency = higher confidence
        - More reliable sources = higher confidence
        - More ticks in window = higher confidence
        
        Returns:
            Confidence score between 0.0 and 1.0
        """
        # Base confidence from profit (0-5 pips maps to 0.2-0.9)
        profit_conf = min(0.9, 0.2 + (profit_pips / 5.0) * 0.7)
        
        # Latency penalty (higher latency = lower confidence)
        latency_penalty = min(0.3, latency_ms / 300.0)
        
        # Source reliability bonus
        reliabilities = [
            self.config.source_reliabilities.get(s, 0.8)
            for s in sources
        ]
        reliability_score = sum(reliabilities) / len(reliabilities) if reliabilities else 0.8
        
        # Tick density bonus (more ticks = better price discovery)
        tick_count = window.total_tick_count
        density_bonus = min(0.1, tick_count / 100.0)
        
        confidence = (profit_conf - latency_penalty) * reliability_score + density_bonus
        
        return max(0.0, min(1.0, confidence))
    
    def get_stats(self) -> Dict[str, Any]:
        """Get engine statistics."""
        return {
            "windows_analyzed": self._windows_analyzed,
            "opportunities_detected": self._opportunities_detected,
            "opportunities_by_type": {
                t.value: c for t, c in self._opportunities_by_type.items()
            },
            "config": {
                "min_profit_pips": self.config.min_profit_pips,
                "min_confidence": self.config.min_confidence,
                "max_latency_risk_ms": self.config.max_latency_risk_ms,
            },
        }
