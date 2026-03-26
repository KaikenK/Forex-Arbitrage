"""
Centralized Control State System

Provides a single source of truth for all runtime configuration that can
be dynamically updated without service restart. All detection engines,
filters, and UI components consume this state.

Design Decisions:
- Observable pattern for reactive updates
- Thread-safe state mutations
- Validation on all updates
- Audit trail for configuration changes
- No service restart required for updates
"""

import time
import logging
import threading
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum
from copy import deepcopy

logger = logging.getLogger(__name__)


class FilterMode(str, Enum):
    """How to apply filters."""
    ANNOTATE = "annotate"  # Add metadata but don't hide
    FILTER = "filter"      # Hide from results
    HIGHLIGHT = "highlight"  # Emphasize matching items


@dataclass
class DetectionThresholds:
    """
    Thresholds for arbitrage detection.
    
    These can be adjusted at runtime to tune sensitivity.
    """
    min_profit_pips: float = 1.0
    min_confidence: float = 0.4
    min_persistence_ms: int = 50
    alignment_window_ms: int = 50
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PersistenceThresholds:
    """
    Thresholds for opportunity persistence classification.
    """
    ephemeral_max_ms: int = 50
    flickering_max_ms: int = 300
    min_detections_persistent: int = 3
    gap_tolerance_ms: int = 500
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass 
class ExecutionFilterSettings:
    """
    Settings for execution feasibility filtering.
    """
    enabled: bool = True
    mode: FilterMode = FilterMode.ANNOTATE
    viable_threshold: float = 70.0
    risky_threshold: float = 40.0
    base_slippage_pips: float = 0.3
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode.value,
            "viable_threshold": self.viable_threshold,
            "risky_threshold": self.risky_threshold,
            "base_slippage_pips": self.base_slippage_pips,
        }


@dataclass
class SessionFilterSettings:
    """
    Settings for session-based filtering.
    """
    enabled_sessions: List[str] = field(default_factory=lambda: ["TOKYO", "LONDON", "NEW_YORK"])
    session_weights: Dict[str, float] = field(default_factory=lambda: {
        "TOKYO": 0.75,
        "LONDON": 1.0,
        "NEW_YORK": 0.95,
    })
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled_sessions": self.enabled_sessions.copy(),
            "session_weights": self.session_weights.copy(),
        }


@dataclass
class DisplaySettings:
    """
    UI display preferences.
    """
    show_ephemeral: bool = False
    show_execution_unlikely: bool = True
    max_opportunities_shown: int = 20
    refresh_rate_ms: int = 250
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ControlState:
    """
    Complete control state for the system.
    
    This is the single source of truth consumed by all components.
    """
    # Active configuration
    active_symbols: List[str] = field(default_factory=lambda: ["USDINR"])
    detection: DetectionThresholds = field(default_factory=DetectionThresholds)
    persistence: PersistenceThresholds = field(default_factory=PersistenceThresholds)
    execution: ExecutionFilterSettings = field(default_factory=ExecutionFilterSettings)
    session: SessionFilterSettings = field(default_factory=SessionFilterSettings)
    display: DisplaySettings = field(default_factory=DisplaySettings)
    
    # Metadata
    version: int = 1
    last_updated_ts: int = 0
    last_updated_by: str = "system"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "active_symbols": self.active_symbols.copy(),
            "detection": self.detection.to_dict(),
            "persistence": self.persistence.to_dict(),
            "execution": self.execution.to_dict(),
            "session": self.session.to_dict(),
            "display": self.display.to_dict(),
            "version": self.version,
            "last_updated_ts": self.last_updated_ts,
            "last_updated_by": self.last_updated_by,
        }


class ControlStateManager:
    """
    Manages the centralized control state with reactive updates.
    
    Components register as observers and receive notifications
    when state changes. Updates are validated and atomic.
    
    Example:
        manager = ControlStateManager()
        
        # Register observer
        def on_state_change(state, changes):
            print(f"State updated: {changes}")
        manager.add_observer(on_state_change)
        
        # Update thresholds
        manager.update({
            "detection": {"min_profit_pips": 1.5}
        })
        
        # Get current state
        state = manager.get_state()
    """
    
    def __init__(self, initial_state: Optional[ControlState] = None):
        """
        Initialize the state manager.
        
        Args:
            initial_state: Optional initial state (uses defaults if None)
        """
        self._state = initial_state or ControlState()
        self._state.last_updated_ts = int(time.time() * 1000)
        self._lock = threading.RLock()
        self._observers: List[Callable[[ControlState, Dict[str, Any]], None]] = []
        self._change_history: List[Dict[str, Any]] = []
        
        logger.info("[ControlStateManager] Initialized with default state")
    
    def add_observer(self, callback: Callable[[ControlState, Dict[str, Any]], None]) -> None:
        """
        Register an observer for state changes.
        
        Callback receives (new_state, changes_dict).
        
        Args:
            callback: Function to call on state changes
        """
        with self._lock:
            self._observers.append(callback)
    
    def remove_observer(self, callback: Callable) -> None:
        """Remove an observer."""
        with self._lock:
            if callback in self._observers:
                self._observers.remove(callback)
    
    def get_state(self) -> ControlState:
        """
        Get the current control state.
        
        Returns a deep copy to prevent accidental mutations.
        """
        with self._lock:
            return deepcopy(self._state)
    
    def get_state_dict(self) -> Dict[str, Any]:
        """Get current state as dictionary."""
        with self._lock:
            return self._state.to_dict()
    
    def update(
        self,
        updates: Dict[str, Any],
        updated_by: str = "api",
    ) -> Dict[str, Any]:
        """
        Update the control state with validated changes.
        
        Args:
            updates: Dictionary of updates (nested keys supported)
            updated_by: Identifier of who made the change
        
        Returns:
            Dictionary of actual changes made
        
        Raises:
            ValueError: If updates are invalid
        """
        with self._lock:
            changes = {}
            
            # Apply updates to each section
            if "active_symbols" in updates:
                symbols = updates["active_symbols"]
                if isinstance(symbols, list):
                    self._state.active_symbols = [s.upper() for s in symbols]
                    changes["active_symbols"] = self._state.active_symbols
            
            if "detection" in updates:
                changes["detection"] = self._update_detection(updates["detection"])
            
            if "persistence" in updates:
                changes["persistence"] = self._update_persistence(updates["persistence"])
            
            if "execution" in updates:
                changes["execution"] = self._update_execution(updates["execution"])
            
            if "session" in updates:
                changes["session"] = self._update_session(updates["session"])
            
            if "display" in updates:
                changes["display"] = self._update_display(updates["display"])
            
            if changes:
                # Update metadata
                self._state.version += 1
                self._state.last_updated_ts = int(time.time() * 1000)
                self._state.last_updated_by = updated_by
                
                # Record change
                self._change_history.append({
                    "version": self._state.version,
                    "timestamp": self._state.last_updated_ts,
                    "updated_by": updated_by,
                    "changes": changes,
                })
                
                # Keep history bounded
                if len(self._change_history) > 100:
                    self._change_history = self._change_history[-100:]
                
                logger.info(f"[ControlStateManager] State updated v{self._state.version} by {updated_by}: {list(changes.keys())}")
                
                # Notify observers
                self._notify_observers(changes)
            
            return changes
    
    def _update_detection(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update detection thresholds."""
        changes = {}
        d = self._state.detection
        
        if "min_profit_pips" in updates:
            val = float(updates["min_profit_pips"])
            if 0 <= val <= 10:
                d.min_profit_pips = val
                changes["min_profit_pips"] = val
        
        if "min_confidence" in updates:
            val = float(updates["min_confidence"])
            if 0 <= val <= 1:
                d.min_confidence = val
                changes["min_confidence"] = val
        
        if "min_persistence_ms" in updates:
            val = int(updates["min_persistence_ms"])
            if 0 <= val <= 5000:
                d.min_persistence_ms = val
                changes["min_persistence_ms"] = val
        
        if "alignment_window_ms" in updates:
            val = int(updates["alignment_window_ms"])
            if 10 <= val <= 500:
                d.alignment_window_ms = val
                changes["alignment_window_ms"] = val
        
        return changes
    
    def _update_persistence(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update persistence thresholds."""
        changes = {}
        p = self._state.persistence
        
        if "ephemeral_max_ms" in updates:
            val = int(updates["ephemeral_max_ms"])
            if 10 <= val <= 500:
                p.ephemeral_max_ms = val
                changes["ephemeral_max_ms"] = val
        
        if "flickering_max_ms" in updates:
            val = int(updates["flickering_max_ms"])
            if 50 <= val <= 2000:
                p.flickering_max_ms = val
                changes["flickering_max_ms"] = val
        
        if "min_detections_persistent" in updates:
            val = int(updates["min_detections_persistent"])
            if 1 <= val <= 20:
                p.min_detections_persistent = val
                changes["min_detections_persistent"] = val
        
        if "gap_tolerance_ms" in updates:
            val = int(updates["gap_tolerance_ms"])
            if 100 <= val <= 2000:
                p.gap_tolerance_ms = val
                changes["gap_tolerance_ms"] = val
        
        return changes
    
    def _update_execution(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update execution filter settings."""
        changes = {}
        e = self._state.execution
        
        if "enabled" in updates:
            e.enabled = bool(updates["enabled"])
            changes["enabled"] = e.enabled
        
        if "mode" in updates:
            mode_str = updates["mode"]
            if mode_str in [m.value for m in FilterMode]:
                e.mode = FilterMode(mode_str)
                changes["mode"] = e.mode.value
        
        if "viable_threshold" in updates:
            val = float(updates["viable_threshold"])
            if 0 <= val <= 100:
                e.viable_threshold = val
                changes["viable_threshold"] = val
        
        if "risky_threshold" in updates:
            val = float(updates["risky_threshold"])
            if 0 <= val <= 100:
                e.risky_threshold = val
                changes["risky_threshold"] = val
        
        if "base_slippage_pips" in updates:
            val = float(updates["base_slippage_pips"])
            if 0 <= val <= 5:
                e.base_slippage_pips = val
                changes["base_slippage_pips"] = val
        
        return changes
    
    def _update_session(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update session filter settings."""
        changes = {}
        s = self._state.session
        
        if "enabled_sessions" in updates:
            sessions = updates["enabled_sessions"]
            if isinstance(sessions, list):
                s.enabled_sessions = [sess.upper() for sess in sessions]
                changes["enabled_sessions"] = s.enabled_sessions
        
        if "session_weights" in updates:
            weights = updates["session_weights"]
            if isinstance(weights, dict):
                for sess, weight in weights.items():
                    if 0 <= float(weight) <= 1:
                        s.session_weights[sess.upper()] = float(weight)
                changes["session_weights"] = s.session_weights.copy()
        
        return changes
    
    def _update_display(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update display settings."""
        changes = {}
        d = self._state.display
        
        if "show_ephemeral" in updates:
            d.show_ephemeral = bool(updates["show_ephemeral"])
            changes["show_ephemeral"] = d.show_ephemeral
        
        if "show_execution_unlikely" in updates:
            d.show_execution_unlikely = bool(updates["show_execution_unlikely"])
            changes["show_execution_unlikely"] = d.show_execution_unlikely
        
        if "max_opportunities_shown" in updates:
            val = int(updates["max_opportunities_shown"])
            if 1 <= val <= 100:
                d.max_opportunities_shown = val
                changes["max_opportunities_shown"] = val
        
        if "refresh_rate_ms" in updates:
            val = int(updates["refresh_rate_ms"])
            if 100 <= val <= 5000:
                d.refresh_rate_ms = val
                changes["refresh_rate_ms"] = val
        
        return changes
    
    def _notify_observers(self, changes: Dict[str, Any]) -> None:
        """Notify all observers of state change."""
        state_copy = deepcopy(self._state)
        for observer in self._observers:
            try:
                observer(state_copy, changes)
            except Exception as e:
                logger.error(f"[ControlStateManager] Observer error: {e}")
    
    def get_change_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent change history."""
        with self._lock:
            return self._change_history[-limit:]
    
    def reset_to_defaults(self, updated_by: str = "system") -> Dict[str, Any]:
        """Reset all settings to defaults."""
        with self._lock:
            old_version = self._state.version
            self._state = ControlState()
            self._state.version = old_version + 1
            self._state.last_updated_ts = int(time.time() * 1000)
            self._state.last_updated_by = updated_by
            
            changes = {"reset": True, "all_sections": True}
            self._notify_observers(changes)
            
            logger.info(f"[ControlStateManager] Reset to defaults by {updated_by}")
            return changes


# Global singleton instance
_control_state_manager: Optional[ControlStateManager] = None


def get_control_state_manager() -> ControlStateManager:
    """Get the global control state manager instance."""
    global _control_state_manager
    if _control_state_manager is None:
        _control_state_manager = ControlStateManager()
    return _control_state_manager


def get_control_state() -> ControlState:
    """Convenience function to get current control state."""
    return get_control_state_manager().get_state()
