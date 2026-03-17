"""
Opportunity Persistence Tracker

Tracks arbitrage opportunities over time, aggregating repeated detections
into evolving entities with persistence metrics. This transforms flickering
sub-second signals into institutionally actionable intelligence.

Design Decisions:
- Opportunities are keyed by (symbol, buy_source, sell_source, type)
- Multiple detections update a single entity rather than creating duplicates
- Persistence classification guides execution feasibility
- Events emitted only on meaningful state changes
"""

import time
import logging
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict

from backend.core.arbitrage.arbitrage_engine import ArbitrageOpportunity, ArbitrageType

logger = logging.getLogger(__name__)


class PersistenceClass(str, Enum):
    """
    Classification of opportunity persistence.
    
    Based on cumulative duration and continuity of detection.
    """
    EPHEMERAL = "ephemeral"      # < 50ms - noise, not actionable
    FLICKERING = "flickering"    # 50-300ms - potential, needs monitoring
    PERSISTENT = "persistent"    # > 300ms continuous - institutionally relevant


@dataclass
class TrackedOpportunity:
    """
    An arbitrage opportunity tracked over time.
    
    Aggregates multiple detections into a single evolving entity
    with persistence metrics for institutional decision-making.
    
    Attributes:
        key: Unique identifier (symbol|buy_source|sell_source|type)
        symbol: Currency pair
        buy_source: Source to buy from
        sell_source: Source to sell to
        opportunity_type: Type of arbitrage (cross_source, session, etc.)
        session: Trading session when first detected
        first_seen_ts: Unix timestamp (ms) of first detection
        last_seen_ts: Unix timestamp (ms) of most recent detection
        cumulative_duration_ms: Total time opportunity was active
        detection_count: Number of times opportunity was detected
        max_profit_pips_seen: Highest profit observed
        current_profit_pips: Most recent profit level
        last_confidence: Most recent confidence score
        gap_count: Number of detection gaps (for flickering assessment)
        max_gap_ms: Longest gap between detections
        persistence_class: Classification (ephemeral/flickering/persistent)
        stability_score: 0-1 score indicating consistency
        is_active: Whether opportunity is currently being detected
    """
    key: str
    symbol: str
    buy_source: str
    sell_source: str
    opportunity_type: ArbitrageType
    session: str
    first_seen_ts: int
    last_seen_ts: int
    cumulative_duration_ms: int = 0
    detection_count: int = 1
    max_profit_pips_seen: float = 0.0
    current_profit_pips: float = 0.0
    last_confidence: float = 0.0
    gap_count: int = 0
    max_gap_ms: int = 0
    persistence_class: PersistenceClass = PersistenceClass.EPHEMERAL
    stability_score: float = 0.0
    is_active: bool = True
    
    # Internal tracking
    _last_detection_ts: int = field(default=0, repr=False)
    _continuous_duration_ms: int = field(default=0, repr=False)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "key": self.key,
            "symbol": self.symbol,
            "buy_source": self.buy_source,
            "sell_source": self.sell_source,
            "type": self.opportunity_type.value,
            "session": self.session,
            "first_seen_ts": self.first_seen_ts,
            "last_seen_ts": self.last_seen_ts,
            "persistence_ms": self.cumulative_duration_ms,
            "persistence_class": self.persistence_class.value,
            "stability_score": round(self.stability_score, 3),
            "detection_count": self.detection_count,
            "max_profit_pips": round(self.max_profit_pips_seen, 2),
            "current_profit_pips": round(self.current_profit_pips, 2),
            "confidence": round(self.last_confidence, 3),
            "gap_count": self.gap_count,
            "is_active": self.is_active,
            "age_ms": int(time.time() * 1000) - self.first_seen_ts,
        }


@dataclass
class TrackerConfig:
    """
    Configuration for opportunity tracking.
    
    Attributes:
        ephemeral_threshold_ms: Duration below which = ephemeral
        persistent_threshold_ms: Duration above which = persistent
        gap_tolerance_ms: Max gap before considering opportunity ended
        stale_timeout_ms: Time after which inactive opportunities are archived
        min_detections_for_persistent: Minimum detections needed for persistent
    """
    ephemeral_threshold_ms: int = 50
    persistent_threshold_ms: int = 300
    gap_tolerance_ms: int = 500  # Allow 500ms gaps for flickering
    stale_timeout_ms: int = 5000  # Archive after 5s of inactivity
    min_detections_for_persistent: int = 3


class OpportunityTracker:
    """
    Tracks and aggregates arbitrage opportunities over time.
    
    Transforms raw opportunity detections into persistence-aware
    entities suitable for institutional decision-making.
    
    Example:
        tracker = OpportunityTracker()
        tracker.on_state_change(lambda opp, event: print(f"{event}: {opp.key}"))
        
        # Process incoming opportunities
        for opp in detected_opportunities:
            tracked = tracker.update(opp)
            if tracked.persistence_class == PersistenceClass.PERSISTENT:
                print(f"Actionable: {tracked.key}")
    """
    
    def __init__(self, config: Optional[TrackerConfig] = None):
        """
        Initialize the tracker.
        
        Args:
            config: Tracker configuration
        """
        self.config = config or TrackerConfig()
        
        # Active opportunities (currently being detected)
        self._active: Dict[str, TrackedOpportunity] = {}
        
        # Recently ended opportunities (for history/analysis)
        self._archived: Dict[str, TrackedOpportunity] = {}
        
        # State change callbacks
        self._callbacks: List[Callable[[TrackedOpportunity, str], Any]] = []
        
        # Stats
        self._total_tracked = 0
        self._total_persistent = 0
        self._total_ephemeral = 0
        
        logger.info(f"[OpportunityTracker] Initialized with config: "
                   f"ephemeral<{config.ephemeral_threshold_ms}ms, "
                   f"persistent>{config.persistent_threshold_ms}ms")
    
    def on_state_change(self, callback: Callable[[TrackedOpportunity, str], Any]) -> None:
        """
        Register a callback for opportunity state changes.
        
        Callback receives (TrackedOpportunity, event_type) where event_type is:
        - "new": First detection of opportunity
        - "updated": Opportunity metrics updated
        - "upgraded": Persistence class upgraded
        - "ended": Opportunity no longer detected
        - "archived": Opportunity moved to archive
        
        Args:
            callback: Function to call on state changes
        """
        self._callbacks.append(callback)
    
    def update(self, opportunity: ArbitrageOpportunity) -> TrackedOpportunity:
        """
        Update tracking with a new opportunity detection.
        
        If the opportunity already exists, updates its metrics.
        If new, creates a tracking entity.
        
        Args:
            opportunity: Detected arbitrage opportunity
        
        Returns:
            The tracked opportunity entity
        """
        current_ts = int(time.time() * 1000)
        key = self._make_key(opportunity)
        
        if key in self._active:
            # Update existing opportunity
            tracked = self._update_existing(key, opportunity, current_ts)
            event = "updated"
            
            # Check for persistence upgrade
            old_class = tracked.persistence_class
            self._classify_persistence(tracked)
            if tracked.persistence_class != old_class:
                event = "upgraded"
                if tracked.persistence_class == PersistenceClass.PERSISTENT:
                    self._total_persistent += 1
                    logger.info(f"[OpportunityTracker] PERSISTENT: {key} "
                              f"({tracked.cumulative_duration_ms}ms, "
                              f"{tracked.detection_count} detections)")
        else:
            # Check if it was recently archived
            if key in self._archived:
                # Reactivate from archive
                tracked = self._archived.pop(key)
                tracked.is_active = True
                tracked.last_seen_ts = current_ts
                tracked._last_detection_ts = current_ts
                tracked.detection_count += 1
                tracked.gap_count += 1
                self._active[key] = tracked
                event = "reactivated"
            else:
                # Create new tracking entity
                tracked = self._create_new(opportunity, current_ts)
                self._active[key] = tracked
                self._total_tracked += 1
                event = "new"
        
        # Emit state change
        self._emit_state_change(tracked, event)
        
        return tracked
    
    def tick(self) -> List[TrackedOpportunity]:
        """
        Periodic tick to update tracking state.
        
        Should be called regularly (e.g., every 100ms) to:
        - Detect ended opportunities
        - Archive stale opportunities
        - Update continuous durations
        
        Returns:
            List of opportunities that ended this tick
        """
        current_ts = int(time.time() * 1000)
        ended = []
        to_archive = []
        
        for key, tracked in list(self._active.items()):
            time_since_detection = current_ts - tracked._last_detection_ts
            
            if time_since_detection > self.config.gap_tolerance_ms:
                # Opportunity ended
                tracked.is_active = False
                ended.append(tracked)
                self._emit_state_change(tracked, "ended")
                
                # Classify final persistence
                self._classify_persistence(tracked)
                if tracked.persistence_class == PersistenceClass.EPHEMERAL:
                    self._total_ephemeral += 1
                
                to_archive.append(key)
            else:
                # Update continuous duration
                tracked._continuous_duration_ms = current_ts - tracked.first_seen_ts
        
        # Move ended to archive
        for key in to_archive:
            tracked = self._active.pop(key)
            self._archived[key] = tracked
        
        # Clean old archives
        self._cleanup_archives(current_ts)
        
        return ended
    
    def get_active(self) -> List[TrackedOpportunity]:
        """Get all currently active opportunities."""
        return list(self._active.values())
    
    def get_persistent(self) -> List[TrackedOpportunity]:
        """Get only persistent opportunities."""
        return [
            t for t in self._active.values()
            if t.persistence_class == PersistenceClass.PERSISTENT
        ]
    
    def get_by_class(self, persistence_class: PersistenceClass) -> List[TrackedOpportunity]:
        """Get opportunities by persistence class."""
        return [
            t for t in self._active.values()
            if t.persistence_class == persistence_class
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get tracker statistics."""
        active_by_class = defaultdict(int)
        for t in self._active.values():
            active_by_class[t.persistence_class.value] += 1
        
        return {
            "active_count": len(self._active),
            "archived_count": len(self._archived),
            "total_tracked": self._total_tracked,
            "total_persistent": self._total_persistent,
            "total_ephemeral": self._total_ephemeral,
            "active_by_class": dict(active_by_class),
            "config": {
                "ephemeral_threshold_ms": self.config.ephemeral_threshold_ms,
                "persistent_threshold_ms": self.config.persistent_threshold_ms,
                "gap_tolerance_ms": self.config.gap_tolerance_ms,
            },
        }
    
    def _make_key(self, opp: ArbitrageOpportunity) -> str:
        """Create unique key for opportunity."""
        symbols = "|".join(sorted(opp.symbols))
        sources = "|".join(sorted(opp.sources))
        return f"{symbols}|{opp.buy_source}|{opp.sell_source}|{opp.type.value}"
    
    def _create_new(self, opp: ArbitrageOpportunity, ts: int) -> TrackedOpportunity:
        """Create a new tracked opportunity."""
        return TrackedOpportunity(
            key=self._make_key(opp),
            symbol=opp.symbols[0] if opp.symbols else "UNKNOWN",
            buy_source=opp.buy_source,
            sell_source=opp.sell_source,
            opportunity_type=opp.type,
            session=opp.session,
            first_seen_ts=ts,
            last_seen_ts=ts,
            cumulative_duration_ms=0,
            detection_count=1,
            max_profit_pips_seen=opp.estimated_profit_pips,
            current_profit_pips=opp.estimated_profit_pips,
            last_confidence=opp.confidence_score,
            _last_detection_ts=ts,
        )
    
    def _update_existing(
        self,
        key: str,
        opp: ArbitrageOpportunity,
        ts: int
    ) -> TrackedOpportunity:
        """Update an existing tracked opportunity."""
        tracked = self._active[key]
        
        # Calculate time since last detection
        gap_ms = ts - tracked._last_detection_ts
        
        # Update duration (add time since last detection)
        if gap_ms < self.config.gap_tolerance_ms:
            tracked.cumulative_duration_ms += gap_ms
        else:
            # Gap too large, record it
            tracked.gap_count += 1
            tracked.max_gap_ms = max(tracked.max_gap_ms, gap_ms)
        
        # Update timestamps
        tracked.last_seen_ts = ts
        tracked._last_detection_ts = ts
        tracked.detection_count += 1
        
        # Update profit tracking
        tracked.current_profit_pips = opp.estimated_profit_pips
        tracked.max_profit_pips_seen = max(
            tracked.max_profit_pips_seen,
            opp.estimated_profit_pips
        )
        tracked.last_confidence = opp.confidence_score
        
        return tracked
    
    def _classify_persistence(self, tracked: TrackedOpportunity) -> None:
        """Classify opportunity persistence level."""
        duration = tracked.cumulative_duration_ms
        detections = tracked.detection_count
        gaps = tracked.gap_count
        
        # Calculate stability score
        # Higher score = more stable/consistent detections
        if tracked.cumulative_duration_ms > 0:
            # Stability penalized by gaps relative to detections
            gap_penalty = min(1.0, gaps / max(1, detections) * 2)
            duration_factor = min(1.0, duration / self.config.persistent_threshold_ms)
            detection_factor = min(1.0, detections / self.config.min_detections_for_persistent)
            tracked.stability_score = (duration_factor + detection_factor) / 2 * (1 - gap_penalty * 0.5)
        else:
            tracked.stability_score = 0.0
        
        # Classify based on duration and detections
        if duration < self.config.ephemeral_threshold_ms:
            tracked.persistence_class = PersistenceClass.EPHEMERAL
        elif duration >= self.config.persistent_threshold_ms and detections >= self.config.min_detections_for_persistent:
            tracked.persistence_class = PersistenceClass.PERSISTENT
        else:
            tracked.persistence_class = PersistenceClass.FLICKERING
    
    def _emit_state_change(self, tracked: TrackedOpportunity, event: str) -> None:
        """Emit state change to callbacks."""
        for callback in self._callbacks:
            try:
                callback(tracked, event)
            except Exception as e:
                logger.error(f"[OpportunityTracker] Callback error: {e}")
    
    def _cleanup_archives(self, current_ts: int) -> None:
        """Remove stale archived opportunities."""
        stale_keys = [
            key for key, tracked in self._archived.items()
            if current_ts - tracked.last_seen_ts > self.config.stale_timeout_ms
        ]
        for key in stale_keys:
            del self._archived[key]
