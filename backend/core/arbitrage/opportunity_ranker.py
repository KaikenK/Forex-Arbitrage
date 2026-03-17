"""
Opportunity Ranker Module - Institutional Grade

Ranks arbitrage opportunities using a composite institutional score that
considers profitability, persistence, execution feasibility, and session
factors to produce actionable intelligence.

Design Decisions:
- Composite scoring formula optimized for institutional decision-making
- Human-readable ranking reasons for every score
- Integration with persistence tracking and execution assessment
- Session weighting reflects real FX market liquidity patterns
- All scoring logic transparent and auditable
"""

import logging
import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
from enum import Enum

from backend.core.arbitrage.arbitrage_engine import ArbitrageOpportunity, ArbitrageType

logger = logging.getLogger(__name__)


class RankingDimension(str, Enum):
    """Dimensions for institutional opportunity ranking."""
    PROFITABILITY = "profitability"
    PERSISTENCE = "persistence"
    EXECUTION_FEASIBILITY = "execution_feasibility"
    SESSION_WEIGHT = "session_weight"
    CONFIDENCE = "confidence"
    LATENCY_RISK = "latency_risk"
    SOURCE_RELIABILITY = "source_reliability"


@dataclass
class InstitutionalScore:
    """
    Detailed institutional scoring breakdown.
    
    Provides full transparency into how the composite score was calculated.
    """
    composite_score: float
    dimension_scores: Dict[RankingDimension, float]
    ranking_reason: str
    score_breakdown: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "composite_score": round(self.composite_score, 1),
            "dimension_scores": {k.value: round(v, 1) for k, v in self.dimension_scores.items()},
            "ranking_reason": self.ranking_reason,
            "score_breakdown": self.score_breakdown,
        }


@dataclass
class RankedOpportunity:
    """
    An arbitrage opportunity with institutional ranking.
    
    Attributes:
        opportunity: The underlying arbitrage opportunity
        composite_score: Overall institutional score (0-100)
        dimension_scores: Individual scores per dimension
        ranking_reason: Human-readable explanation
        rank: Position in ranked list (1 = best)
        persistence_count: Detection count
        persistence_class: ephemeral/flickering/persistent
        execution_verdict: viable/risky/unlikely
        first_seen_ms: When first detected
        last_seen_ms: When last seen
    """
    opportunity: ArbitrageOpportunity
    composite_score: float
    dimension_scores: Dict[RankingDimension, float]
    ranking_reason: str = ""
    rank: int = 0
    persistence_count: int = 1
    persistence_class: str = "ephemeral"
    execution_verdict: str = "unknown"
    execution_reasons: List[str] = field(default_factory=list)
    first_seen_ms: int = 0
    last_seen_ms: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "opportunity": self.opportunity.to_dict(),
            "composite_score": round(self.composite_score, 2),
            "dimension_scores": {
                k.value: round(v, 2) for k, v in self.dimension_scores.items()
            },
            "ranking_reason": self.ranking_reason,
            "rank": self.rank,
            "persistence_count": self.persistence_count,
            "persistence_class": self.persistence_class,
            "execution_verdict": self.execution_verdict,
            "execution_reasons": self.execution_reasons,
            "first_seen_ms": self.first_seen_ms,
            "last_seen_ms": self.last_seen_ms,
            "duration_ms": self.last_seen_ms - self.first_seen_ms,
        }


@dataclass
class RankingConfig:
    """
    Configuration for institutional opportunity ranking.
    
    Weights reflect institutional priorities:
    - Profitability matters but isn't everything
    - Persistence is critical for execution confidence
    - Execution feasibility determines if action is possible
    - Session weights reflect real FX liquidity
    
    Attributes:
        weights: Importance weights for each dimension (should sum to ~1.0)
        min_composite_score: Minimum score to include in results
        max_results: Maximum opportunities to return
        persistence_window_ms: Time window for tracking persistence
        session_weights: Institutional session importance (London/NY > Tokyo)
    """
    weights: Dict[RankingDimension, float] = field(default_factory=lambda: {
        RankingDimension.PROFITABILITY: 0.25,
        RankingDimension.PERSISTENCE: 0.25,
        RankingDimension.EXECUTION_FEASIBILITY: 0.20,
        RankingDimension.SESSION_WEIGHT: 0.15,
        RankingDimension.CONFIDENCE: 0.10,
        RankingDimension.LATENCY_RISK: 0.05,
    })
    min_composite_score: float = 30.0
    max_results: int = 50
    persistence_window_ms: int = 5000  # 5 seconds
    
    # Session weights for institutional ranking (London/NY overlap is best)
    session_weights: Dict[str, float] = field(default_factory=lambda: {
        "LONDON": 1.0,          # Primary FX session
        "NEW_YORK": 0.95,       # High USD liquidity
        "LONDON_NY": 1.0,       # Overlap - best liquidity
        "TOKYO": 0.75,          # Lower liquidity for USD/INR
        "TOKYO_LONDON": 0.85,   # Overlap
        "UNKNOWN": 0.5,
    })
    
    # Persistence class scores
    persistence_scores: Dict[str, float] = field(default_factory=lambda: {
        "ephemeral": 20.0,
        "flickering": 50.0,
        "persistent": 100.0,
    })
    
    # Execution verdict scores
    execution_verdict_scores: Dict[str, float] = field(default_factory=lambda: {
        "viable": 100.0,
        "risky": 50.0,
        "unlikely": 10.0,
        "unknown": 30.0,
    })


class OpportunityRanker:
    """
    Institutional-grade opportunity ranker.
    
    Produces composite scores that reflect:
    - Profit potential (adjusted for costs)
    - Persistence (stable vs flickering)
    - Execution feasibility (can we actually trade it?)
    - Session importance (London/NY > Tokyo)
    
    Every score includes a human-readable explanation.
    
    Example:
        ranker = OpportunityRanker(config=RankingConfig(
            min_composite_score=40.0,
            max_results=20
        ))
        
        # Process new opportunities with context
        ranked = ranker.rank_with_context(
            opportunities,
            persistence_data={...},
            execution_assessments={...}
        )
        
        # Get top opportunity with full explanation
        best = ranked[0] if ranked else None
        print(f"Best: {best.opportunity.symbols}")
        print(f"Score: {best.composite_score}")
        print(f"Reason: {best.ranking_reason}")
    """
    
    def __init__(
        self,
        config: Optional[RankingConfig] = None,
        source_reliabilities: Optional[Dict[str, float]] = None,
    ):
        """
        Initialize the ranker.
        
        Args:
            config: Ranking configuration
            source_reliabilities: Reliability scores per source (0-1)
        """
        self.config = config or RankingConfig()
        self.source_reliabilities = source_reliabilities or {}
        
        # Track opportunity persistence
        # Key: opportunity signature (symbols + sources + type)
        self._persistence_tracker: Dict[str, Dict[str, Any]] = {}
        
        # Stats
        self._opportunities_ranked = 0
        self._opportunities_filtered = 0
        
        logger.info(f"[OpportunityRanker] Initialized with institutional scoring")
    
    def set_source_reliability(self, source_id: str, reliability: float) -> None:
        """
        Set reliability score for a source.
        
        Args:
            source_id: Data source identifier
            reliability: Reliability score (0.0 to 1.0)
        """
        self.source_reliabilities[source_id] = max(0.0, min(1.0, reliability))
    
    def rank(
        self,
        opportunities: List[ArbitrageOpportunity],
    ) -> List[RankedOpportunity]:
        """
        Rank a list of opportunities.
        
        Args:
            opportunities: List of detected opportunities
        
        Returns:
            Sorted list of ranked opportunities (best first)
        """
        if not opportunities:
            return []
        
        current_time_ms = int(time.time() * 1000)
        ranked: List[RankedOpportunity] = []
        
        # Clean up old persistence data
        self._cleanup_persistence(current_time_ms)
        
        for opp in opportunities:
            # Calculate dimension scores
            dimension_scores = self._calculate_dimension_scores(opp)
            
            # Calculate composite score
            composite = self._calculate_composite_score(dimension_scores)
            
            # Track persistence
            signature = self._get_signature(opp)
            persistence = self._update_persistence(signature, opp, current_time_ms)
            
            # Apply persistence bonus
            if persistence["count"] > 1:
                persistence_bonus = min(10.0, persistence["count"] * 2.0)
                composite += persistence_bonus
            
            # Create ranked opportunity
            ranked_opp = RankedOpportunity(
                opportunity=opp,
                composite_score=composite,
                dimension_scores=dimension_scores,
                persistence_count=persistence["count"],
                first_seen_ms=persistence["first_seen"],
                last_seen_ms=current_time_ms,
            )
            
            ranked.append(ranked_opp)
            self._opportunities_ranked += 1
        
        # Filter by minimum score
        filtered = [
            r for r in ranked 
            if r.composite_score >= self.config.min_composite_score
        ]
        self._opportunities_filtered += len(ranked) - len(filtered)
        
        # Sort by composite score (descending)
        filtered.sort(key=lambda r: r.composite_score, reverse=True)
        
        # Assign ranks
        for i, r in enumerate(filtered):
            r.rank = i + 1
        
        # Limit results
        return filtered[:self.config.max_results]
    
    def rank_with_context(
        self,
        opportunities: List[ArbitrageOpportunity],
        persistence_data: Optional[Dict[str, Dict[str, Any]]] = None,
        execution_assessments: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[RankedOpportunity]:
        """
        Rank opportunities with full institutional context.
        
        This enhanced ranking method integrates:
        - Persistence tracking data
        - Execution feasibility assessments
        - Session importance weights
        
        Args:
            opportunities: List of detected opportunities
            persistence_data: Dict keyed by opportunity key with persistence info
            execution_assessments: Dict keyed by opportunity key with execution assessments
        
        Returns:
            Sorted list of ranked opportunities with full explanations
        """
        if not opportunities:
            return []
        
        current_time_ms = int(time.time() * 1000)
        ranked: List[RankedOpportunity] = []
        persistence_data = persistence_data or {}
        execution_assessments = execution_assessments or {}
        
        for opp in opportunities:
            key = self._make_opportunity_key(opp)
            
            # Get persistence info
            persist = persistence_data.get(key, {})
            persistence_class = persist.get("persistence_class", "ephemeral")
            stability_score = persist.get("stability_score", 0.0)
            detection_count = persist.get("detection_count", 1)
            
            # Get execution assessment
            exec_data = execution_assessments.get(key, {})
            exec_verdict = exec_data.get("verdict", "unknown")
            exec_feasibility = exec_data.get("feasibility_score", 50.0)
            exec_reasons = exec_data.get("verdict_reasons", [])
            
            # Calculate institutional dimension scores
            dimension_scores, score_breakdown = self._calculate_institutional_scores(
                opp=opp,
                persistence_class=persistence_class,
                stability_score=stability_score,
                exec_verdict=exec_verdict,
                exec_feasibility=exec_feasibility,
            )
            
            # Calculate weighted composite score
            composite = self._calculate_weighted_composite(dimension_scores)
            
            # Generate ranking reason
            ranking_reason = self._generate_ranking_reason(
                composite=composite,
                dimension_scores=dimension_scores,
                persistence_class=persistence_class,
                exec_verdict=exec_verdict,
                opp=opp,
            )
            
            # Create ranked opportunity
            ranked_opp = RankedOpportunity(
                opportunity=opp,
                composite_score=composite,
                dimension_scores=dimension_scores,
                ranking_reason=ranking_reason,
                persistence_count=detection_count,
                persistence_class=persistence_class,
                execution_verdict=exec_verdict,
                execution_reasons=exec_reasons,
                first_seen_ms=persist.get("first_seen_ts", current_time_ms),
                last_seen_ms=current_time_ms,
            )
            
            ranked.append(ranked_opp)
            self._opportunities_ranked += 1
        
        # Filter and sort
        filtered = [r for r in ranked if r.composite_score >= self.config.min_composite_score]
        self._opportunities_filtered += len(ranked) - len(filtered)
        filtered.sort(key=lambda r: r.composite_score, reverse=True)
        
        # Assign ranks
        for i, r in enumerate(filtered):
            r.rank = i + 1
        
        return filtered[:self.config.max_results]
    
    def _make_opportunity_key(self, opp: ArbitrageOpportunity) -> str:
        """Create unique key for opportunity."""
        symbols = "|".join(sorted(opp.symbols))
        return f"{symbols}|{opp.buy_source}|{opp.sell_source}|{opp.type.value}"
    
    def _calculate_institutional_scores(
        self,
        opp: ArbitrageOpportunity,
        persistence_class: str,
        stability_score: float,
        exec_verdict: str,
        exec_feasibility: float,
    ) -> Tuple[Dict[RankingDimension, float], List[str]]:
        """
        Calculate institutional dimension scores with explanations.
        
        Returns:
            Tuple of (dimension_scores, score_breakdown)
        """
        scores = {}
        breakdown = []
        
        # 1. Profitability (0-100)
        profit_pips = max(0, opp.estimated_profit_pips)
        profit_score = min(100, profit_pips * 20)  # 5 pips = 100
        scores[RankingDimension.PROFITABILITY] = profit_score
        breakdown.append(f"Profit: {profit_pips:.2f} pips → {profit_score:.0f}/100")
        
        # 2. Persistence (0-100 based on class)
        persistence_score = self.config.persistence_scores.get(persistence_class, 20.0)
        persistence_score = persistence_score * (0.7 + 0.3 * stability_score)  # Stability bonus
        scores[RankingDimension.PERSISTENCE] = min(100, persistence_score)
        breakdown.append(f"Persistence: {persistence_class} → {persistence_score:.0f}/100")
        
        # 3. Execution Feasibility (0-100)
        exec_score = self.config.execution_verdict_scores.get(exec_verdict, 30.0)
        # Blend with actual feasibility score
        exec_score = (exec_score * 0.6) + (exec_feasibility * 0.4)
        scores[RankingDimension.EXECUTION_FEASIBILITY] = min(100, exec_score)
        breakdown.append(f"Execution: {exec_verdict} → {exec_score:.0f}/100")
        
        # 4. Session Weight (0-100)
        session = opp.session.upper() if opp.session else "UNKNOWN"
        session_weight = self.config.session_weights.get(session, 0.5)
        session_score = session_weight * 100
        scores[RankingDimension.SESSION_WEIGHT] = session_score
        breakdown.append(f"Session: {session} → {session_score:.0f}/100")
        
        # 5. Confidence (0-100)
        confidence_score = opp.confidence_score * 100
        scores[RankingDimension.CONFIDENCE] = confidence_score
        breakdown.append(f"Confidence: {opp.confidence_score:.2f} → {confidence_score:.0f}/100")
        
        # 6. Latency Risk (inverse - lower latency = higher score)
        latency = opp.latency_risk_ms
        latency_score = max(0, 100 - (latency / 2))  # 200ms = 0
        scores[RankingDimension.LATENCY_RISK] = latency_score
        breakdown.append(f"Latency: {latency:.0f}ms → {latency_score:.0f}/100")
        
        return scores, breakdown
    
    def _calculate_weighted_composite(self, scores: Dict[RankingDimension, float]) -> float:
        """Calculate weighted composite score."""
        composite = 0.0
        total_weight = 0.0
        
        for dimension, weight in self.config.weights.items():
            if dimension in scores:
                composite += scores[dimension] * weight
                total_weight += weight
        
        if total_weight > 0:
            composite /= total_weight
            composite *= 100 / 100  # Normalize to 0-100
        
        return min(100, max(0, composite))
    
    def _generate_ranking_reason(
        self,
        composite: float,
        dimension_scores: Dict[RankingDimension, float],
        persistence_class: str,
        exec_verdict: str,
        opp: ArbitrageOpportunity,
    ) -> str:
        """Generate human-readable ranking explanation."""
        reasons = []
        
        # Lead with the verdict
        if composite >= 70:
            reasons.append(f"Strong opportunity (score: {composite:.0f})")
        elif composite >= 50:
            reasons.append(f"Moderate opportunity (score: {composite:.0f})")
        else:
            reasons.append(f"Weak opportunity (score: {composite:.0f})")
        
        # Key factors
        if persistence_class == "persistent":
            reasons.append("Persistent signal")
        elif persistence_class == "ephemeral":
            reasons.append("Ephemeral - timing critical")
        
        if exec_verdict == "viable":
            reasons.append("Execution viable")
        elif exec_verdict == "risky":
            reasons.append("Execution risky")
        elif exec_verdict == "unlikely":
            reasons.append("Execution unlikely")
        
        # Profit highlight
        profit = opp.estimated_profit_pips
        if profit >= 2.0:
            reasons.append(f"High profit ({profit:.1f} pips)")
        
        return " | ".join(reasons)

    def _calculate_dimension_scores(
        self,
        opp: ArbitrageOpportunity,
    ) -> Dict[RankingDimension, float]:
        """
        Calculate individual dimension scores for an opportunity.
        
        All scores are normalized to 0-100 range.
        
        Args:
            opp: Arbitrage opportunity to score
        
        Returns:
            Dict of dimension scores
        """
        scores = {}
        
        # Profitability: 0-10 pips maps to 0-100
        profit_pips = max(0, opp.estimated_profit_pips)
        scores[RankingDimension.PROFITABILITY] = min(100, profit_pips * 10)
        
        # Latency Risk: Lower is better (0-200ms maps to 100-0)
        latency = opp.latency_risk_ms
        scores[RankingDimension.LATENCY_RISK] = max(0, 100 - (latency / 2))
        
        # Confidence: Already 0-1, map to 0-100
        scores[RankingDimension.CONFIDENCE] = opp.confidence_score * 100
        
        # Source Reliability: Average of source reliabilities
        reliabilities = [
            self.source_reliabilities.get(s, 0.7)
            for s in opp.sources
        ]
        avg_reliability = sum(reliabilities) / len(reliabilities) if reliabilities else 0.7
        scores[RankingDimension.SOURCE_RELIABILITY] = avg_reliability * 100
        
        # Session Liquidity: Based on current session
        session = opp.session
        liquidity = self.config.session_liquidity_scores.get(session, 0.5)
        scores[RankingDimension.SESSION_LIQUIDITY] = liquidity * 100
        
        # Persistence: Starts at 50, increases with consecutive detections
        # (Updated later based on persistence tracker)
        scores[RankingDimension.PERSISTENCE] = 50.0
        
        return scores
    
    def _calculate_composite_score(
        self,
        dimension_scores: Dict[RankingDimension, float],
    ) -> float:
        """
        Calculate weighted composite score.
        
        Args:
            dimension_scores: Individual dimension scores
        
        Returns:
            Composite score (0-100)
        """
        composite = 0.0
        total_weight = 0.0
        
        for dimension, weight in self.config.weights.items():
            if dimension in dimension_scores:
                composite += dimension_scores[dimension] * weight
                total_weight += weight
        
        # Normalize if weights don't sum to 1
        if total_weight > 0 and total_weight != 1.0:
            composite /= total_weight
        
        return composite
    
    def _get_signature(self, opp: ArbitrageOpportunity) -> str:
        """
        Generate a unique signature for an opportunity.
        
        Used to track persistence across windows.
        
        Args:
            opp: Arbitrage opportunity
        
        Returns:
            Signature string
        """
        symbols = "-".join(sorted(opp.symbols))
        sources = "-".join(sorted(opp.sources))
        return f"{opp.type.value}_{symbols}_{sources}"
    
    def _update_persistence(
        self,
        signature: str,
        opp: ArbitrageOpportunity,
        current_time_ms: int,
    ) -> Dict[str, Any]:
        """
        Update persistence tracking for an opportunity.
        
        Args:
            signature: Opportunity signature
            opp: The opportunity
            current_time_ms: Current timestamp
        
        Returns:
            Persistence data dict
        """
        if signature in self._persistence_tracker:
            tracker = self._persistence_tracker[signature]
            tracker["count"] += 1
            tracker["last_seen"] = current_time_ms
            tracker["last_profit"] = opp.estimated_profit_pips
        else:
            tracker = {
                "count": 1,
                "first_seen": current_time_ms,
                "last_seen": current_time_ms,
                "last_profit": opp.estimated_profit_pips,
            }
            self._persistence_tracker[signature] = tracker
        
        return tracker
    
    def _cleanup_persistence(self, current_time_ms: int) -> None:
        """
        Remove stale entries from persistence tracker.
        
        Args:
            current_time_ms: Current timestamp
        """
        window = self.config.persistence_window_ms
        stale_signatures = [
            sig for sig, data in self._persistence_tracker.items()
            if current_time_ms - data["last_seen"] > window
        ]
        
        for sig in stale_signatures:
            del self._persistence_tracker[sig]
    
    def get_persistent_opportunities(
        self,
        min_persistence: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Get opportunities that have persisted across multiple windows.
        
        Args:
            min_persistence: Minimum persistence count
        
        Returns:
            List of persistent opportunity info dicts
        """
        current_time_ms = int(time.time() * 1000)
        window = self.config.persistence_window_ms
        
        persistent = []
        for sig, data in self._persistence_tracker.items():
            if data["count"] >= min_persistence:
                age_ms = current_time_ms - data["first_seen"]
                if age_ms <= window * 2:  # Within 2x persistence window
                    persistent.append({
                        "signature": sig,
                        "count": data["count"],
                        "duration_ms": age_ms,
                        "last_profit_pips": data["last_profit"],
                    })
        
        return sorted(persistent, key=lambda p: p["count"], reverse=True)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get ranker statistics."""
        return {
            "opportunities_ranked": self._opportunities_ranked,
            "opportunities_filtered": self._opportunities_filtered,
            "active_persistence_entries": len(self._persistence_tracker),
            "source_reliabilities": self.source_reliabilities.copy(),
            "config": {
                "min_composite_score": self.config.min_composite_score,
                "max_results": self.config.max_results,
                "weights": {k.value: v for k, v in self.config.weights.items()},
            },
        }
