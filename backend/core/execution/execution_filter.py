"""
Simulated Execution Filter

Assesses the execution feasibility of arbitrage opportunities by modeling
real-world execution constraints: latency, slippage, spreads, and liquidity.

This is an ADVISORY component - it annotates opportunities with execution
reality scores but does NOT filter them out. The decision to act remains
with the institutional user.

Design Decisions:
- All opportunities pass through with annotations
- Slippage modeled conservatively (overestimate costs)
- Session liquidity affects feasibility scores
- Verdicts are explanatory, not prescriptive
"""

import logging
from typing import Dict, Optional, Any, List
from dataclasses import dataclass, field
from enum import Enum

from backend.core.arbitrage.arbitrage_engine import ArbitrageOpportunity, ArbitrageType
from backend.core.arbitrage.opportunity_tracker import TrackedOpportunity, PersistenceClass

logger = logging.getLogger(__name__)


class ExecutionVerdict(str, Enum):
    """
    Execution feasibility verdict.
    
    These are advisory classifications, not hard filters.
    """
    VIABLE = "viable"        # High probability of profitable execution
    RISKY = "risky"          # Execution possible but uncertain profitability
    UNLIKELY = "unlikely"    # Execution likely unprofitable after costs


@dataclass
class ExecutionAssessment:
    """
    Assessment of execution feasibility for an opportunity.
    
    Attributes:
        opportunity_key: Key linking to the tracked opportunity
        expected_slippage_pips: Estimated slippage cost in pips
        execution_feasibility_score: 0-100 score (higher = more feasible)
        execution_verdict: Advisory classification
        verdict_reasons: Human-readable explanation of verdict
        net_expected_profit_pips: Profit after estimated costs
        latency_cost_pips: Estimated cost due to latency
        spread_cost_pips: Estimated cost due to spreads
        liquidity_factor: Session liquidity multiplier (0-1)
    """
    opportunity_key: str
    expected_slippage_pips: float
    execution_feasibility_score: float
    execution_verdict: ExecutionVerdict
    verdict_reasons: List[str]
    net_expected_profit_pips: float
    latency_cost_pips: float
    spread_cost_pips: float
    liquidity_factor: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "opportunity_key": self.opportunity_key,
            "expected_slippage_pips": round(self.expected_slippage_pips, 3),
            "feasibility_score": round(self.execution_feasibility_score, 1),
            "verdict": self.execution_verdict.value,
            "verdict_reasons": self.verdict_reasons,
            "net_expected_profit_pips": round(self.net_expected_profit_pips, 3),
            "cost_breakdown": {
                "latency_pips": round(self.latency_cost_pips, 3),
                "spread_pips": round(self.spread_cost_pips, 3),
                "slippage_pips": round(self.expected_slippage_pips, 3),
            },
            "liquidity_factor": round(self.liquidity_factor, 2),
        }


@dataclass
class ExecutionFilterConfig:
    """
    Configuration for execution feasibility assessment.
    
    Attributes:
        base_slippage_pips: Minimum expected slippage
        latency_slippage_factor: Slippage increase per ms of latency
        spread_cost_factor: What fraction of spread is execution cost
        session_liquidity: Liquidity scores by session (0-1)
        viable_threshold: Minimum score for VIABLE verdict
        risky_threshold: Minimum score for RISKY verdict (below = UNLIKELY)
        persistence_bonus: Score bonus per persistence class
    """
    base_slippage_pips: float = 0.3
    latency_slippage_factor: float = 0.01  # 0.01 pips per ms
    spread_cost_factor: float = 0.5  # Half the spread is cost
    session_liquidity: Dict[str, float] = field(default_factory=lambda: {
        "TOKYO": 0.7,
        "LONDON": 1.0,
        "NEW_YORK": 0.9,
        "LONDON_NY_OVERLAP": 1.0,
        "TOKYO_LONDON_OVERLAP": 0.85,
        "UNKNOWN": 0.5,
    })
    viable_threshold: float = 70.0
    risky_threshold: float = 40.0
    persistence_bonus: Dict[str, float] = field(default_factory=lambda: {
        PersistenceClass.EPHEMERAL.value: 0.0,
        PersistenceClass.FLICKERING.value: 10.0,
        PersistenceClass.PERSISTENT.value: 25.0,
    })


class SimulatedExecutionFilter:
    """
    Assesses execution feasibility of arbitrage opportunities.
    
    This component models real-world execution constraints and produces
    advisory assessments. It does NOT filter opportunities - all
    opportunities pass through with execution annotations.
    
    Example:
        filter = SimulatedExecutionFilter()
        
        for tracked_opp in tracker.get_active():
            assessment = filter.assess(tracked_opp, source_spreads, source_latencies)
            if assessment.execution_verdict == ExecutionVerdict.VIABLE:
                print(f"Actionable: {tracked_opp.key}")
            else:
                print(f"Advisory: {assessment.verdict_reasons}")
    """
    
    def __init__(self, config: Optional[ExecutionFilterConfig] = None):
        """
        Initialize the execution filter.
        
        Args:
            config: Filter configuration
        """
        self.config = config or ExecutionFilterConfig()
        
        # Cache for source characteristics
        self._source_latencies: Dict[str, float] = {}
        self._source_spreads: Dict[str, float] = {}
        
        # Stats
        self._assessments_made = 0
        self._viable_count = 0
        self._risky_count = 0
        self._unlikely_count = 0
        
        logger.info(f"[SimulatedExecutionFilter] Initialized with "
                   f"viable>{self.config.viable_threshold}, "
                   f"risky>{self.config.risky_threshold}")
    
    def set_source_latency(self, source_id: str, latency_ms: float) -> None:
        """Set known latency for a source."""
        self._source_latencies[source_id] = latency_ms
    
    def set_source_spread(self, source_id: str, spread_pips: float) -> None:
        """Set current spread for a source."""
        self._source_spreads[source_id] = spread_pips
    
    def assess(
        self,
        tracked: TrackedOpportunity,
        buy_spread_pips: Optional[float] = None,
        sell_spread_pips: Optional[float] = None,
        buy_latency_ms: Optional[float] = None,
        sell_latency_ms: Optional[float] = None,
    ) -> ExecutionAssessment:
        """
        Assess execution feasibility of a tracked opportunity.
        
        Args:
            tracked: The tracked opportunity to assess
            buy_spread_pips: Current spread at buy source (optional)
            sell_spread_pips: Current spread at sell source (optional)
            buy_latency_ms: Latency to buy source (optional)
            sell_latency_ms: Latency to sell source (optional)
        
        Returns:
            ExecutionAssessment with feasibility analysis
        """
        self._assessments_made += 1
        
        # Get or estimate parameters
        buy_spread = buy_spread_pips or self._source_spreads.get(tracked.buy_source, 2.0)
        sell_spread = sell_spread_pips or self._source_spreads.get(tracked.sell_source, 2.0)
        buy_lat = buy_latency_ms or self._source_latencies.get(tracked.buy_source, 50.0)
        sell_lat = sell_latency_ms or self._source_latencies.get(tracked.sell_source, 50.0)
        
        # Calculate costs
        latency_cost = self._calculate_latency_cost(buy_lat, sell_lat)
        spread_cost = self._calculate_spread_cost(buy_spread, sell_spread)
        slippage = self._calculate_slippage(tracked, buy_lat + sell_lat)
        
        total_cost = latency_cost + spread_cost + slippage
        
        # Get liquidity factor
        session = tracked.session.upper() if tracked.session else "UNKNOWN"
        liquidity = self.config.session_liquidity.get(session, 0.5)
        
        # Calculate net profit
        gross_profit = tracked.current_profit_pips
        net_profit = gross_profit - total_cost
        
        # Calculate feasibility score
        score = self._calculate_feasibility_score(
            net_profit=net_profit,
            gross_profit=gross_profit,
            liquidity=liquidity,
            persistence_class=tracked.persistence_class,
            stability=tracked.stability_score,
        )
        
        # Determine verdict
        verdict, reasons = self._determine_verdict(
            score=score,
            net_profit=net_profit,
            gross_profit=gross_profit,
            latency_cost=latency_cost,
            spread_cost=spread_cost,
            slippage=slippage,
            liquidity=liquidity,
            persistence_class=tracked.persistence_class,
        )
        
        # Update stats
        if verdict == ExecutionVerdict.VIABLE:
            self._viable_count += 1
        elif verdict == ExecutionVerdict.RISKY:
            self._risky_count += 1
        else:
            self._unlikely_count += 1
        
        return ExecutionAssessment(
            opportunity_key=tracked.key,
            expected_slippage_pips=slippage,
            execution_feasibility_score=score,
            execution_verdict=verdict,
            verdict_reasons=reasons,
            net_expected_profit_pips=net_profit,
            latency_cost_pips=latency_cost,
            spread_cost_pips=spread_cost,
            liquidity_factor=liquidity,
        )
    
    def assess_opportunity(
        self,
        opportunity: ArbitrageOpportunity,
        persistence_class: PersistenceClass = PersistenceClass.EPHEMERAL,
        stability_score: float = 0.0,
    ) -> ExecutionAssessment:
        """
        Assess a raw opportunity (without tracking metadata).
        
        Args:
            opportunity: Raw arbitrage opportunity
            persistence_class: Known persistence class
            stability_score: Known stability score
        
        Returns:
            ExecutionAssessment
        """
        # Create a minimal tracked representation
        tracked = TrackedOpportunity(
            key=f"{opportunity.symbols[0]}|{opportunity.buy_source}|{opportunity.sell_source}",
            symbol=opportunity.symbols[0] if opportunity.symbols else "UNKNOWN",
            buy_source=opportunity.buy_source,
            sell_source=opportunity.sell_source,
            opportunity_type=opportunity.type,
            session=opportunity.session,
            first_seen_ts=opportunity.timestamp_ms,
            last_seen_ts=opportunity.timestamp_ms,
            current_profit_pips=opportunity.estimated_profit_pips,
            max_profit_pips_seen=opportunity.estimated_profit_pips,
            last_confidence=opportunity.confidence_score,
            persistence_class=persistence_class,
            stability_score=stability_score,
        )
        
        return self.assess(tracked)
    
    def _calculate_latency_cost(self, buy_latency: float, sell_latency: float) -> float:
        """
        Estimate cost due to execution latency.
        
        Higher latency = more price movement = higher cost.
        """
        total_latency = buy_latency + sell_latency
        return total_latency * self.config.latency_slippage_factor
    
    def _calculate_spread_cost(self, buy_spread: float, sell_spread: float) -> float:
        """
        Estimate cost due to bid-ask spreads.
        
        We pay the spread on both legs of the trade.
        """
        return (buy_spread + sell_spread) * self.config.spread_cost_factor * 0.5
    
    def _calculate_slippage(self, tracked: TrackedOpportunity, total_latency: float) -> float:
        """
        Estimate execution slippage.
        
        Slippage increases with:
        - Base slippage (market microstructure)
        - Latency (price can move)
        - Opportunity ephemerality (fast-moving)
        """
        base = self.config.base_slippage_pips
        
        # Ephemeral opportunities have higher slippage risk
        persistence_multiplier = {
            PersistenceClass.EPHEMERAL: 2.0,
            PersistenceClass.FLICKERING: 1.3,
            PersistenceClass.PERSISTENT: 1.0,
        }.get(tracked.persistence_class, 1.5)
        
        # Latency component
        latency_slippage = total_latency * self.config.latency_slippage_factor * 0.5
        
        return (base + latency_slippage) * persistence_multiplier
    
    def _calculate_feasibility_score(
        self,
        net_profit: float,
        gross_profit: float,
        liquidity: float,
        persistence_class: PersistenceClass,
        stability: float,
    ) -> float:
        """
        Calculate overall feasibility score (0-100).
        
        Components:
        - Profit ratio (net/gross) - 40%
        - Liquidity factor - 20%
        - Persistence bonus - 25%
        - Stability score - 15%
        """
        # Profit ratio component (0-40)
        if gross_profit > 0:
            profit_ratio = max(0, net_profit / gross_profit)
            profit_score = min(40, profit_ratio * 40)
        else:
            profit_score = 0
        
        # Absolute profit component (bonus for high profit)
        if net_profit > 2.0:
            profit_score = min(40, profit_score + 10)
        
        # Liquidity component (0-20)
        liquidity_score = liquidity * 20
        
        # Persistence bonus (0-25)
        persistence_bonus = self.config.persistence_bonus.get(
            persistence_class.value, 0
        )
        
        # Stability component (0-15)
        stability_score = stability * 15
        
        total = profit_score + liquidity_score + persistence_bonus + stability_score
        return min(100, max(0, total))
    
    def _determine_verdict(
        self,
        score: float,
        net_profit: float,
        gross_profit: float,
        latency_cost: float,
        spread_cost: float,
        slippage: float,
        liquidity: float,
        persistence_class: PersistenceClass,
    ) -> tuple[ExecutionVerdict, List[str]]:
        """
        Determine execution verdict with explanatory reasons.
        """
        reasons = []
        
        # Determine verdict
        if score >= self.config.viable_threshold:
            verdict = ExecutionVerdict.VIABLE
            reasons.append(f"High feasibility score ({score:.0f}/100)")
            if net_profit > 0:
                reasons.append(f"Positive expected profit: {net_profit:.2f} pips")
            if persistence_class == PersistenceClass.PERSISTENT:
                reasons.append("Persistent opportunity (>300ms)")
            if liquidity >= 0.9:
                reasons.append("High session liquidity")
        
        elif score >= self.config.risky_threshold:
            verdict = ExecutionVerdict.RISKY
            reasons.append(f"Moderate feasibility score ({score:.0f}/100)")
            
            # Explain the risks
            if net_profit < gross_profit * 0.5:
                reasons.append(f"Execution costs erode {((gross_profit - net_profit) / gross_profit * 100):.0f}% of profit")
            if persistence_class == PersistenceClass.EPHEMERAL:
                reasons.append("Ephemeral opportunity - high timing risk")
            if latency_cost > 0.5:
                reasons.append(f"Latency cost significant: {latency_cost:.2f} pips")
        
        else:
            verdict = ExecutionVerdict.UNLIKELY
            reasons.append(f"Low feasibility score ({score:.0f}/100)")
            
            # Explain why unlikely
            if net_profit <= 0:
                reasons.append(f"Expected net loss: {net_profit:.2f} pips after costs")
            if persistence_class == PersistenceClass.EPHEMERAL:
                reasons.append("Ephemeral - too fast to execute reliably")
            total_cost = latency_cost + spread_cost + slippage
            if total_cost > gross_profit:
                reasons.append(f"Costs ({total_cost:.2f} pips) exceed gross profit ({gross_profit:.2f} pips)")
            if liquidity < 0.6:
                reasons.append(f"Low session liquidity ({liquidity:.0%})")
        
        return verdict, reasons
    
    def get_stats(self) -> Dict[str, Any]:
        """Get filter statistics."""
        total = self._viable_count + self._risky_count + self._unlikely_count
        return {
            "assessments_made": self._assessments_made,
            "verdict_distribution": {
                "viable": self._viable_count,
                "risky": self._risky_count,
                "unlikely": self._unlikely_count,
            },
            "verdict_percentages": {
                "viable": f"{self._viable_count / max(1, total) * 100:.1f}%",
                "risky": f"{self._risky_count / max(1, total) * 100:.1f}%",
                "unlikely": f"{self._unlikely_count / max(1, total) * 100:.1f}%",
            } if total > 0 else {},
            "config": {
                "viable_threshold": self.config.viable_threshold,
                "risky_threshold": self.config.risky_threshold,
            },
        }
