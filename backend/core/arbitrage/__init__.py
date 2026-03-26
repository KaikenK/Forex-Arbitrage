"""
Arbitrage Package

Contains the core arbitrage detection engine components:
- Time alignment / micro-batching
- Cross-source arbitrage detection
- Triangular arbitrage detection  
- Session-based inefficiency detection
- Opportunity ranking and scoring
- Opportunity persistence tracking
- Diagnostics and explanations
"""

from backend.core.arbitrage.tick_aligner import TickAligner, AlignedTickWindow
from backend.core.arbitrage.arbitrage_engine import ArbitrageEngine, ArbitrageOpportunity
from backend.core.arbitrage.opportunity_ranker import OpportunityRanker, RankingConfig, RankedOpportunity
from backend.core.arbitrage.opportunity_tracker import (
    OpportunityTracker,
    TrackerConfig,
    TrackedOpportunity,
    PersistenceClass,
)
from backend.core.arbitrage.diagnostics import (
    ArbitrageDiagnosticsEngine,
    ArbitrageDiagnostic,
    DiagnosticThresholds,
    SpreadAnalysis,
    LatencyAnalysis,
)

__all__ = [
    "TickAligner",
    "AlignedTickWindow",
    "ArbitrageEngine",
    "ArbitrageOpportunity",
    "OpportunityRanker",
    "RankingConfig",
    "RankedOpportunity",
    "OpportunityTracker",
    "TrackerConfig",
    "TrackedOpportunity",
    "PersistenceClass",
    "ArbitrageDiagnosticsEngine",
    "ArbitrageDiagnostic",
    "DiagnosticThresholds",
    "SpreadAnalysis",
    "LatencyAnalysis",
]
