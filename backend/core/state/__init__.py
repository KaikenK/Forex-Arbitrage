"""
State Management Package

Centralized state store and arbitrage state machine for the FX arbitrage platform.
Provides a single source of truth for all system state.
Includes centralized control state for runtime configuration.
"""

from .state_store import StateStore, SymbolState, SourceState
from .arbitrage_state_machine import (
    ArbitrageStateMachine,
    ArbitrageState,
    ArbitrageStateTransition,
    StateMachineConfig,
)
from .metrics import (
    TimeWeightedMetrics,
    EMACalculator,
    StabilityScore,
    ArbitrageMetrics,
)
from .control_state import (
    ControlState,
    ControlStateManager,
    DetectionThresholds,
    PersistenceThresholds,
    ExecutionFilterSettings,
    SessionFilterSettings,
    DisplaySettings,
    FilterMode,
    get_control_state,
    get_control_state_manager,
)

__all__ = [
    "StateStore",
    "SymbolState",
    "SourceState",
    "ArbitrageStateMachine",
    "ArbitrageState",
    "ArbitrageStateTransition",
    "StateMachineConfig",
    "TimeWeightedMetrics",
    "EMACalculator",
    "StabilityScore",
    "ArbitrageMetrics",
    # Control State
    "ControlState",
    "ControlStateManager",
    "DetectionThresholds",
    "PersistenceThresholds",
    "ExecutionFilterSettings",
    "SessionFilterSettings",
    "DisplaySettings",
    "FilterMode",
    "get_control_state",
    "get_control_state_manager",
]
