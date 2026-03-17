"""
INR/USD Composite Instrument Handler

Handles USDINR as a multi-source composite instrument since MT5 doesn't
reliably provide INR pairs via most retail brokers.

Design Decisions:
- Treats INR/USD as a logical instrument from multiple sources
- Supports MT5 proxy, REST APIs, simulated feeds
- Tracks session-specific pricing (Asia session is primary for INR)
- No assumption of single authoritative price
- Designed for research and arbitrage detection, not execution
"""

import time
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict
import threading

from backend.core.interfaces.data_source import DataSourceInterface, DataSourceConfig, RawTick
from backend.core.interfaces.normalized_tick import NormalizedTick, detect_trading_session
from backend.core.session_manager import session_manager, get_current_session_info

logger = logging.getLogger(__name__)


@dataclass
class INRSourceConfig:
    """
    Configuration for an INR price source.
    
    Attributes:
        source_id: Unique source identifier
        source_type: Type of source ("mt5_proxy", "rest_api", "rbi_reference", "simulated")
        weight: Weight for composite price calculation (0.0 to 1.0)
        session_preference: Sessions where this source is preferred
        is_primary_in_asia: Whether this is primary source during Asia session
        spread_adjustment_pips: Adjustment to account for source-specific spreads
    """
    source_id: str
    source_type: str
    weight: float = 1.0
    session_preference: List[str] = field(default_factory=lambda: ["tokyo", "london", "new_york"])
    is_primary_in_asia: bool = False
    spread_adjustment_pips: float = 0.0


@dataclass  
class INRPricePoint:
    """
    A price point for INR from a specific source.
    
    Attributes:
        source_id: Source identifier
        bid: Bid price
        ask: Ask price
        mid: Mid price
        timestamp_ms: Timestamp in milliseconds
        session: Trading session at time of quote
        latency_ms: Estimated latency
        is_stale: Whether this quote is considered stale
    """
    source_id: str
    bid: float
    ask: float
    mid: float
    timestamp_ms: int
    session: str
    latency_ms: float = 0.0
    is_stale: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "bid": round(self.bid, 4),
            "ask": round(self.ask, 4),
            "mid": round(self.mid, 4),
            "spread_pips": round((self.ask - self.bid) * 100, 2),  # INR pip = 0.01
            "timestamp_ms": self.timestamp_ms,
            "session": self.session,
            "latency_ms": round(self.latency_ms, 1),
            "is_stale": self.is_stale,
        }


class INRInstrumentHandler:
    """
    Handles USDINR as a composite multi-source instrument.
    
    Since MT5 doesn't reliably stream USDINR from most retail brokers,
    this handler aggregates prices from multiple sources:
    
    - MT5 synthetic proxy (derived from other pairs if available)
    - REST API feeds (exchangerate-api, RBI reference rates)
    - Simulated benchmark feed (for research)
    
    Key features:
    - No single authoritative price - all sources are tracked
    - Session-aware (INR primarily trades during Asia/London overlap)
    - Cross-source arbitrage detection ready
    - Research-focused (explains WHY prices differ)
    
    Example:
        handler = INRInstrumentHandler()
        
        # Register sources
        handler.register_source(INRSourceConfig(
            source_id="mt5_proxy",
            source_type="mt5_proxy",
            weight=0.8,
            is_primary_in_asia=False,
        ))
        
        handler.register_source(INRSourceConfig(
            source_id="rbi_reference",
            source_type="rest_api",
            weight=1.0,
            is_primary_in_asia=True,
        ))
        
        # Update prices from sources
        handler.update_price("mt5_proxy", bid=83.20, ask=83.25)
        
        # Get composite view
        view = handler.get_composite_view()
    """
    
    # INR-specific constants
    PIP_VALUE = 0.01  # 1 pip = 0.01 for INR pairs
    STALE_THRESHOLD_MS = 30000  # 30 seconds
    
    # RBI reference rate times (published around 1:30 PM IST = 8:00 UTC)
    RBI_REFERENCE_HOUR_UTC = 8
    
    def __init__(
        self,
        symbol: str = "USDINR",
        stale_threshold_ms: int = 30000,
    ):
        """
        Initialize INR instrument handler.
        
        Args:
            symbol: Symbol name (USDINR or INRUSD)
            stale_threshold_ms: Threshold for considering quotes stale
        """
        self.symbol = symbol
        self._stale_threshold_ms = stale_threshold_ms
        
        # Registered sources
        self._sources: Dict[str, INRSourceConfig] = {}
        
        # Current prices from each source
        self._prices: Dict[str, INRPricePoint] = {}
        
        # Historical prices for analysis
        self._price_history: Dict[str, List[INRPricePoint]] = defaultdict(list)
        self._max_history = 1000
        
        # Cross-source divergence tracking
        self._divergences: List[Dict[str, Any]] = []
        
        # Thread safety
        self._lock = threading.Lock()
        
        # Stats
        self._updates_received = 0
        self._divergences_detected = 0
    
    def register_source(self, config: INRSourceConfig) -> None:
        """
        Register a price source for INR.
        
        Args:
            config: Source configuration
        """
        with self._lock:
            self._sources[config.source_id] = config
            logger.info(f"[INR] Registered source: {config.source_id} ({config.source_type})")
    
    def unregister_source(self, source_id: str) -> None:
        """Unregister a price source."""
        with self._lock:
            if source_id in self._sources:
                del self._sources[source_id]
                if source_id in self._prices:
                    del self._prices[source_id]
    
    def update_price(
        self,
        source_id: str,
        bid: float,
        ask: float,
        timestamp_ms: Optional[int] = None,
        latency_ms: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Update price from a source.
        
        Args:
            source_id: Source identifier
            bid: Bid price
            ask: Ask price
            timestamp_ms: Optional timestamp (uses current time if not provided)
            latency_ms: Estimated source latency
        
        Returns:
            Divergence event if significant divergence detected, None otherwise
        """
        if source_id not in self._sources:
            logger.warning(f"[INR] Unknown source: {source_id}")
            return None
        
        timestamp_ms = timestamp_ms or int(time.time() * 1000)
        session = detect_trading_session(timestamp_ms)
        mid = (bid + ask) / 2
        
        price_point = INRPricePoint(
            source_id=source_id,
            bid=bid,
            ask=ask,
            mid=mid,
            timestamp_ms=timestamp_ms,
            session=session,
            latency_ms=latency_ms,
            is_stale=False,
        )
        
        with self._lock:
            # Store current price
            old_price = self._prices.get(source_id)
            self._prices[source_id] = price_point
            
            # Add to history
            history = self._price_history[source_id]
            history.append(price_point)
            if len(history) > self._max_history:
                history.pop(0)
            
            self._updates_received += 1
            
            # Check for divergence across sources
            divergence = self._check_divergence(price_point)
            
            return divergence
    
    def update_from_tick(self, tick: RawTick) -> Optional[Dict[str, Any]]:
        """
        Update from a RawTick object.
        
        Args:
            tick: Raw tick from a data source
        
        Returns:
            Divergence event if detected
        """
        return self.update_price(
            source_id=tick.source_id,
            bid=tick.bid,
            ask=tick.ask,
            timestamp_ms=tick.timestamp_ms,
        )
    
    def _check_divergence(self, new_price: INRPricePoint) -> Optional[Dict[str, Any]]:
        """
        Check for significant price divergence across sources.
        
        Returns divergence event if mid prices differ by more than threshold.
        """
        if len(self._prices) < 2:
            return None
        
        # Mark stale prices
        current_time = int(time.time() * 1000)
        for source_id, price in self._prices.items():
            age_ms = current_time - price.timestamp_ms
            price.is_stale = age_ms > self._stale_threshold_ms
        
        # Get non-stale prices
        active_prices = [
            (source_id, price) for source_id, price in self._prices.items()
            if not price.is_stale
        ]
        
        if len(active_prices) < 2:
            return None
        
        # Find min/max mid prices
        mids = [(s, p.mid) for s, p in active_prices]
        min_mid = min(mids, key=lambda x: x[1])
        max_mid = max(mids, key=lambda x: x[1])
        
        difference_pips = (max_mid[1] - min_mid[1]) / self.PIP_VALUE
        
        # Only report significant divergences (> 5 pips for INR)
        if difference_pips > 5.0:
            divergence = {
                "type": "inr_divergence",
                "symbol": self.symbol,
                "timestamp_ms": new_price.timestamp_ms,
                "session": new_price.session,
                "high_source": max_mid[0],
                "high_mid": max_mid[1],
                "low_source": min_mid[0],
                "low_mid": min_mid[1],
                "difference_pips": round(difference_pips, 2),
                "all_sources": {s: p.to_dict() for s, p in active_prices},
                "explanation": self._explain_divergence(
                    max_mid[0], min_mid[0], difference_pips, new_price.session
                ),
            }
            
            self._divergences.append(divergence)
            if len(self._divergences) > 100:
                self._divergences.pop(0)
            
            self._divergences_detected += 1
            
            return divergence
        
        return None
    
    def _explain_divergence(
        self,
        high_source: str,
        low_source: str,
        diff_pips: float,
        session: str,
    ) -> str:
        """
        Generate human-readable explanation for divergence.
        
        Research-focused: explains WHY the divergence might exist.
        """
        explanations = []
        
        # Session-based explanation
        if session == "tokyo":
            explanations.append(
                "INR is most liquid during Asia session. "
                "Divergence during Tokyo hours may indicate real market movement."
            )
        elif session in ["london", "new_york"]:
            explanations.append(
                "INR liquidity is lower outside Asia hours. "
                "Wider spreads and delayed quotes from some sources are expected."
            )
        
        # Source-type explanation
        high_config = self._sources.get(high_source)
        low_config = self._sources.get(low_source)
        
        if high_config and low_config:
            if high_config.source_type == "rest_api" and low_config.source_type == "mt5_proxy":
                explanations.append(
                    "REST API sources update less frequently than streaming sources. "
                    "The MT5 proxy may be more current."
                )
            elif high_config.source_type == "simulated":
                explanations.append(
                    "Simulated source is not based on live market data. "
                    "Treat this divergence as synthetic for research purposes."
                )
        
        # RBI reference timing
        session_info = get_current_session_info()
        hour = session_info.get("hour_utc", 0)
        if hour == self.RBI_REFERENCE_HOUR_UTC:
            explanations.append(
                "RBI reference rate is typically published around this time. "
                "Sources may update at different times."
            )
        
        # Magnitude explanation
        if diff_pips > 20:
            explanations.append(
                f"Large divergence ({diff_pips:.1f} pips) suggests potential data quality issue "
                "or significant latency difference between sources."
            )
        elif diff_pips > 10:
            explanations.append(
                f"Moderate divergence ({diff_pips:.1f} pips) may represent arbitrage opportunity "
                "but transaction costs should be considered."
            )
        
        return " ".join(explanations)
    
    def get_composite_view(self) -> Dict[str, Any]:
        """
        Get composite view of INR prices across all sources.
        
        Returns comprehensive view for research and display.
        """
        with self._lock:
            current_time = int(time.time() * 1000)
            session_info = get_current_session_info()
            
            # Update stale status
            active_sources = []
            stale_sources = []
            
            for source_id, price in self._prices.items():
                age_ms = current_time - price.timestamp_ms
                price.is_stale = age_ms > self._stale_threshold_ms
                
                source_data = {
                    **price.to_dict(),
                    "age_ms": age_ms,
                    "config": {
                        "weight": self._sources[source_id].weight,
                        "type": self._sources[source_id].source_type,
                        "is_primary_in_asia": self._sources[source_id].is_primary_in_asia,
                    } if source_id in self._sources else {},
                }
                
                if price.is_stale:
                    stale_sources.append(source_data)
                else:
                    active_sources.append(source_data)
            
            # Calculate spread range across sources
            if active_sources:
                all_bids = [s["bid"] for s in active_sources]
                all_asks = [s["ask"] for s in active_sources]
                best_bid = max(all_bids)
                best_ask = min(all_asks)
                
                # Cross-source spread (if negative, arbitrage exists)
                cross_spread = best_ask - best_bid
                cross_spread_pips = cross_spread / self.PIP_VALUE
            else:
                best_bid = best_ask = cross_spread_pips = None
            
            return {
                "symbol": self.symbol,
                "timestamp_ms": current_time,
                "session": session_info,
                "sources": {
                    "active": active_sources,
                    "stale": stale_sources,
                    "total_registered": len(self._sources),
                },
                "cross_source_analysis": {
                    "best_bid": round(best_bid, 4) if best_bid else None,
                    "best_ask": round(best_ask, 4) if best_ask else None,
                    "cross_spread_pips": round(cross_spread_pips, 2) if cross_spread_pips is not None else None,
                    "arbitrage_present": cross_spread_pips < 0 if cross_spread_pips is not None else False,
                },
                "stats": {
                    "updates_received": self._updates_received,
                    "divergences_detected": self._divergences_detected,
                },
                "recent_divergences": self._divergences[-5:] if self._divergences else [],
            }
    
    def get_source_comparison(self) -> Dict[str, Any]:
        """
        Get detailed comparison between sources.
        
        For research: shows exactly how sources differ.
        """
        with self._lock:
            if len(self._prices) < 2:
                return {
                    "status": "insufficient_sources",
                    "message": f"Need at least 2 sources for comparison, have {len(self._prices)}",
                    "sources": list(self._prices.keys()),
                }
            
            # Build comparison matrix
            sources = list(self._prices.keys())
            matrix = {}
            
            for i, s1 in enumerate(sources):
                for s2 in sources[i+1:]:
                    p1 = self._prices[s1]
                    p2 = self._prices[s2]
                    
                    mid_diff = p1.mid - p2.mid
                    mid_diff_pips = mid_diff / self.PIP_VALUE
                    
                    bid_diff = p1.bid - p2.bid
                    ask_diff = p1.ask - p2.ask
                    
                    time_diff = p1.timestamp_ms - p2.timestamp_ms
                    
                    key = f"{s1}_vs_{s2}"
                    matrix[key] = {
                        "source_1": s1,
                        "source_2": s2,
                        "mid_difference": round(mid_diff, 4),
                        "mid_difference_pips": round(mid_diff_pips, 2),
                        "bid_difference": round(bid_diff, 4),
                        "ask_difference": round(ask_diff, 4),
                        "time_difference_ms": time_diff,
                        "source_1_newer": time_diff > 0,
                    }
            
            return {
                "symbol": self.symbol,
                "source_count": len(sources),
                "comparison_matrix": matrix,
                "individual_sources": {s: self._prices[s].to_dict() for s in sources},
            }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get handler statistics."""
        return {
            "symbol": self.symbol,
            "registered_sources": len(self._sources),
            "active_prices": len([p for p in self._prices.values() if not p.is_stale]),
            "stale_prices": len([p for p in self._prices.values() if p.is_stale]),
            "updates_received": self._updates_received,
            "divergences_detected": self._divergences_detected,
            "divergence_rate": (
                self._divergences_detected / self._updates_received * 100
                if self._updates_received > 0 else 0
            ),
        }


# Factory function to create pre-configured INR handler
def create_inr_handler_with_sources() -> INRInstrumentHandler:
    """
    Create INR handler with default source configurations.
    
    Returns handler ready to receive prices from:
    - MT5 proxy (if available)
    - REST API feed
    - Simulated benchmark
    """
    handler = INRInstrumentHandler(symbol="USDINR")
    
    # MT5 proxy source (may not be available)
    handler.register_source(INRSourceConfig(
        source_id="mt5_proxy",
        source_type="mt5_proxy",
        weight=0.7,
        session_preference=["london", "new_york"],
        is_primary_in_asia=False,
    ))
    
    # REST API source (primary for research)
    handler.register_source(INRSourceConfig(
        source_id="rest_api_primary",
        source_type="rest_api",
        weight=1.0,
        session_preference=["tokyo", "london"],
        is_primary_in_asia=True,
    ))
    
    # Simulated benchmark (for controlled experiments)
    handler.register_source(INRSourceConfig(
        source_id="simulated_benchmark",
        source_type="simulated",
        weight=0.5,
        session_preference=["tokyo", "london", "new_york"],
        is_primary_in_asia=False,
    ))
    
    return handler
