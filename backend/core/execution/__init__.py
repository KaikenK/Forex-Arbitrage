"""
Execution Engine Package

Pluggable execution engine with broker interfaces for the FX arbitrage platform.
Supports paper trading, MT5 execution, and REST broker interfaces.
Includes SimulatedExecutionFilter for feasibility assessment.
"""

from .execution_engine import (
    ExecutionEngine,
    ExecutionConfig,
    ExecutionRequest,
    ExecutionResult,
    ExecutionState,
    RiskLimits,
    AuditEntry,
)
from .brokers import (
    BrokerInterface,
    PaperBroker,
    MT5Broker,
    RESTBroker,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderType,
    OrderStatus,
    Position,
)
from .execution_filter import (
    SimulatedExecutionFilter,
    ExecutionFilterConfig,
    ExecutionAssessment,
    ExecutionVerdict,
)

__all__ = [
    # Execution Engine
    "ExecutionEngine",
    "ExecutionConfig",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionState",
    "RiskLimits",
    "AuditEntry",
    # Brokers
    "BrokerInterface",
    "PaperBroker",
    "MT5Broker",
    "RESTBroker",
    "OrderRequest",
    "OrderResult",
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "Position",
    # Execution Filter
    "SimulatedExecutionFilter",
    "ExecutionFilterConfig",
    "ExecutionAssessment",
    "ExecutionVerdict",
]
