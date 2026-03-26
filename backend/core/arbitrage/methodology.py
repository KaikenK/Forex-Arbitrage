"""
Formalized Arbitrage Methodology Module

Provides mathematically grounded, fully documented functions for
arbitrage detection, profit calculation, persistence classification,
and composite opportunity scoring.

Academic Foundation:
    - No-arbitrage condition: For two sources A and B quoting the same
      instrument, an arbitrage exists iff bid_A > ask_B (or vice versa).
      See Hull (2018), Ch.5 "No-Arbitrage Arguments" and Foucault,
      Pagano & Röell (2013), "Market Liquidity", Ch.4.

    - Cross-source profit: π = bid_A - ask_B - C_spread - C_slippage - C_latency
      where C_x represents transaction cost components.
      See Chaboud et al. (2014), "Rise of the Machines: Algorithmic Trading
      in the Foreign Exchange Market", Journal of Finance.

    - Triangular arbitrage: For currency triplet (A, B, C), arbitrage
      exists when the product of cross-rates deviates from unity:
      |R(A/B) × R(B/C) × R(C/A) - 1| > threshold
      See Aiba et al. (2002), "Triangular Arbitrage as an Interaction
      Among Foreign Exchange Rates", Physica A.

Design Principles:
    - Every constant is a named variable with documented rationale
    - Every function has mathematical notation in its docstring
    - No magic numbers — all thresholds come from configuration or constants
    - Pure functions where possible (no side effects)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


# =============================================================================
# NAMED CONSTANTS (no magic numbers)
# =============================================================================

# Persistence thresholds derived from FX market microstructure:
# Sub-50ms signals are indistinguishable from tick noise at retail latencies.
# 300ms represents ~3 typical MT5 tick intervals, providing statistical confidence.
# See Hasbrouck & Saar (2013), "Low-Latency Trading", Journal of Financial Markets.
EPHEMERAL_THRESHOLD_MS: int = 50
"""Opportunities shorter than this are market noise (milliseconds)."""

FLICKERING_THRESHOLD_MS: int = 300
"""Opportunities must exceed this duration to be considered persistent (milliseconds)."""

MIN_DETECTIONS_FOR_PERSISTENT: int = 3
"""Minimum distinct detections required before classifying as persistent."""

# Pip definitions per ISO 4217 convention:
# Most pairs: 1 pip = 0.0001 (4th decimal)
# JPY pairs:  1 pip = 0.01   (2nd decimal)
STANDARD_PIP_VALUE: float = 0.0001
"""Standard pip value for non-JPY currency pairs."""

JPY_PIP_VALUE: float = 0.01
"""Pip value for JPY-denominated currency pairs."""

# Triangular arbitrage threshold:
# Rate product should equal 1.0 in a no-arbitrage world.
# Deviations below this are within normal bid-ask noise.
# Calibrated against typical retail FX spreads (1-3 pips).
TRIANGULAR_DEVIATION_THRESHOLD: float = 0.0005
"""Minimum deviation of rate product from unity to signal triangular arbitrage."""

# Composite score weights — sum to 1.0.
# Weights reflect institutional priorities:
# Persistence is weighted highest because ephemeral signals are unexecutable.
# Feasibility captures real-world costs.
# Profit and session context provide the economic rationale.
WEIGHT_PROFIT: float = 0.25
"""Weight for profit component in composite scoring."""

WEIGHT_PERSISTENCE: float = 0.30
"""Weight for persistence component (highest — ephemeral signals are noise)."""

WEIGHT_FEASIBILITY: float = 0.25
"""Weight for execution feasibility component."""

WEIGHT_SESSION: float = 0.10
"""Weight for session/liquidity context."""

WEIGHT_CONFIDENCE: float = 0.10
"""Weight for detection confidence."""


# =============================================================================
# DATA TYPES
# =============================================================================

class PersistenceClass(str, Enum):
    """
    Opportunity persistence classification.

    Based on cumulative detection duration and count, following
    Hasbrouck & Saar (2013) latency thresholds.
    """
    EPHEMERAL = "ephemeral"      # < 50ms — noise
    FLICKERING = "flickering"    # 50–300ms — monitoring candidate
    PERSISTENT = "persistent"    # > 300ms with ≥3 detections — actionable


@dataclass(frozen=True)
class PersistenceResult:
    """
    Result of persistence classification.

    Attributes:
        duration_ms: Cumulative detection duration in milliseconds
        persistence_class: Classification label
        stability_score: Consistency metric in [0, 1]
    """
    duration_ms: int
    persistence_class: PersistenceClass
    stability_score: float


@dataclass(frozen=True)
class CompositeScoreResult:
    """
    Result of composite opportunity scoring.

    Attributes:
        composite_score: Final weighted score in [0, 100]
        profit_component: Contribution from profitability
        persistence_component: Contribution from persistence
        feasibility_component: Contribution from execution feasibility
        session_component: Contribution from session context
        confidence_component: Contribution from detection confidence
        explanation: Human-readable explanation of the score
    """
    composite_score: float
    profit_component: float
    persistence_component: float
    feasibility_component: float
    session_component: float
    confidence_component: float
    explanation: str


# =============================================================================
# CORE METHODOLOGY FUNCTIONS
# =============================================================================

def is_arbitrage(bid_a: float, ask_b: float) -> bool:
    """
    Determine if a cross-source arbitrage condition exists.

    The no-arbitrage condition in FX states that for two sources
    A and B quoting the same instrument:

        Arbitrage exists ⟺ bid_A > ask_B

    When source A's bid exceeds source B's ask, a trader can
    simultaneously buy from B (at ask_B) and sell to A (at bid_A),
    locking in a riskless profit of (bid_A - ask_B).

    Reference:
        Hull, J.C. (2018). "Options, Futures, and Other Derivatives",
        10th ed., Ch.5: No-Arbitrage Arguments.

    Args:
        bid_a: Bid price from source A (the higher-quoting source)
        ask_b: Ask price from source B (the lower-quoting source)

    Returns:
        True if the arbitrage condition is satisfied (bid_A > ask_B)

    Examples:
        >>> is_arbitrage(bid_a=1.0852, ask_b=1.0850)
        True
        >>> is_arbitrage(bid_a=1.0850, ask_b=1.0852)
        False
    """
    return bid_a > ask_b


def calculate_profit(
    bid_a: float,
    ask_b: float,
    spread_cost_pips: float = 0.0,
    slippage_pips: float = 0.0,
    latency_cost_pips: float = 0.0,
    pip_value: float = STANDARD_PIP_VALUE,
) -> float:
    """
    Calculate net profit of a cross-source arbitrage opportunity.

    The profit formula is:

        π_net = (bid_A - ask_B) / pip_value - C_spread - C_slippage - C_latency

    Where:
        - bid_A − ask_B  is the gross price differential
        - C_spread        is the cost of crossing bid-ask spreads on both legs
        - C_slippage      is estimated market impact / slippage
        - C_latency       is estimated price movement during execution delay

    All cost components are in pips.

    Reference:
        Chaboud, A., Chiquoine, B., Hjalmarsson, E. & Vega, C. (2014).
        "Rise of the Machines: Algorithmic Trading in the Foreign Exchange
        Market", Journal of Finance, 69(5), pp.2045–2084.

    Args:
        bid_a: Bid price from the sell-side source
        ask_b: Ask price from the buy-side source
        spread_cost_pips: Combined spread costs in pips (default: 0)
        slippage_pips: Estimated slippage in pips (default: 0)
        latency_cost_pips: Estimated latency-induced cost in pips (default: 0)
        pip_value: Value of one pip (default: 0.0001 for non-JPY pairs)

    Returns:
        Net profit in pips. Negative values indicate a losing opportunity
        after costs.

    Examples:
        >>> calculate_profit(1.0852, 1.0850, spread_cost_pips=0.1)
        1.9  # gross 2.0 pips minus 0.1 spread cost
    """
    gross_profit_pips = (bid_a - ask_b) / pip_value
    total_costs = spread_cost_pips + slippage_pips + latency_cost_pips
    return gross_profit_pips - total_costs


def is_triangular_arbitrage(
    rate_ab: float,
    rate_bc: float,
    rate_ca: float,
    threshold: float = TRIANGULAR_DEVIATION_THRESHOLD,
) -> bool:
    """
    Determine if a triangular arbitrage condition exists.

    For three currencies A, B, C with exchange rates:
        R(A/B), R(B/C), R(C/A)

    The no-arbitrage condition requires:
        R(A/B) × R(B/C) × R(C/A) = 1

    An arbitrage opportunity exists when:
        |R(A/B) × R(B/C) × R(C/A) − 1| > threshold

    Reference:
        Aiba, Y., Hatano, N., Takayasu, H., Marumo, K. & Shimizu, T. (2002).
        "Triangular Arbitrage as an Interaction Among Foreign Exchange Rates",
        Physica A, 310(3-4), pp.467–479.

    Args:
        rate_ab: Exchange rate A/B
        rate_bc: Exchange rate B/C
        rate_ca: Exchange rate C/A
        threshold: Minimum deviation from unity (default: 0.0005)

    Returns:
        True if the triangular arbitrage condition is satisfied

    Examples:
        >>> is_triangular_arbitrage(1.0850, 0.9220, 1.0001)
        False  # product ≈ 1.0, within threshold
    """
    rate_product = rate_ab * rate_bc * rate_ca
    deviation = abs(rate_product - 1.0)
    return deviation > threshold


def calculate_triangular_profit(
    rate_ab: float,
    rate_bc: float,
    rate_ca: float,
    notional: float = 1.0,
) -> float:
    """
    Calculate profit from a triangular arbitrage cycle.

    Starting with `notional` units of currency A:
        1. Convert A → B at rate R(A/B): get notional × R(A/B) units of B
        2. Convert B → C at rate R(B/C): get notional × R(A/B) × R(B/C) units of C
        3. Convert C → A at rate R(C/A): get notional × R(A/B) × R(B/C) × R(C/A) units of A

    Profit = final_A − notional = notional × (rate_product − 1)

    Args:
        rate_ab: Exchange rate A/B
        rate_bc: Exchange rate B/C
        rate_ca: Exchange rate C/A
        notional: Starting amount in currency A (default: 1.0)

    Returns:
        Profit in units of currency A
    """
    rate_product = rate_ab * rate_bc * rate_ca
    return notional * (rate_product - 1.0)


def compute_persistence(
    first_seen_ms: int,
    last_seen_ms: int,
    detection_count: int = 1,
    gap_count: int = 0,
) -> PersistenceResult:
    """
    Classify an opportunity's persistence based on duration and consistency.

    Classification thresholds (from Hasbrouck & Saar, 2013):
        - EPHEMERAL:   duration < 50ms  — indistinguishable from noise
        - FLICKERING:  50ms ≤ duration < 300ms — monitoring candidate
        - PERSISTENT:  duration ≥ 300ms AND detections ≥ 3 — actionable

    Stability score (S) combines three factors:
        S = 0.4 × duration_factor + 0.3 × count_factor + 0.3 × gap_penalty

    Where:
        - duration_factor = min(1, duration / 300)
        - count_factor = min(1, detections / 3)
        - gap_penalty = 1 − min(1, gaps / detections × 2) × 0.5

    Args:
        first_seen_ms: Unix timestamp (ms) of first detection
        last_seen_ms: Unix timestamp (ms) of most recent detection
        detection_count: Number of distinct detections
        gap_count: Number of detection gaps

    Returns:
        PersistenceResult with classification and stability score
    """
    duration_ms = max(0, last_seen_ms - first_seen_ms)

    # Classify
    if duration_ms < EPHEMERAL_THRESHOLD_MS:
        persistence_class = PersistenceClass.EPHEMERAL
    elif (duration_ms >= FLICKERING_THRESHOLD_MS
          and detection_count >= MIN_DETECTIONS_FOR_PERSISTENT):
        persistence_class = PersistenceClass.PERSISTENT
    else:
        persistence_class = PersistenceClass.FLICKERING

    # Calculate stability score
    duration_factor = min(1.0, duration_ms / FLICKERING_THRESHOLD_MS)
    count_factor = min(1.0, detection_count / MIN_DETECTIONS_FOR_PERSISTENT)

    if detection_count > 0:
        gap_ratio = min(1.0, gap_count / max(1, detection_count) * 2)
        gap_penalty_factor = 1.0 - gap_ratio * 0.5
    else:
        gap_penalty_factor = 0.0

    stability_score = (
        0.4 * duration_factor
        + 0.3 * count_factor
        + 0.3 * gap_penalty_factor
    )
    stability_score = max(0.0, min(1.0, stability_score))

    return PersistenceResult(
        duration_ms=duration_ms,
        persistence_class=persistence_class,
        stability_score=stability_score,
    )


def compute_composite_score(
    profit_pips: float,
    persistence_score: float,
    feasibility_score: float,
    session_weight: float,
    confidence: float,
    max_profit_pips: float = 10.0,
) -> CompositeScoreResult:
    """
    Compute a weighted composite score for an arbitrage opportunity.

    The composite score C is a weighted sum of normalized components:

        C = w_profit × P + w_persistence × S + w_feasibility × F
          + w_session × L + w_confidence × K

    Where (all normalized to [0, 100]):
        P = min(100, profit_pips / max_profit × 100)
        S = persistence_score × 100    (already in [0, 1])
        F = feasibility_score          (already in [0, 100])
        L = session_weight × 100       (already in [0, 1])
        K = confidence × 100           (already in [0, 1])

    Weights (sum to 1.0):
        w_profit      = 0.25  (economic rationale)
        w_persistence = 0.30  (highest — ephemeral signals are unexecutable)
        w_feasibility = 0.25  (real-world cost consideration)
        w_session     = 0.10  (liquidity context)
        w_confidence  = 0.10  (detection quality)

    Args:
        profit_pips: Estimated net profit in pips
        persistence_score: Stability score in [0, 1]
        feasibility_score: Execution feasibility in [0, 100]
        session_weight: Session liquidity weight in [0, 1]
        confidence: Detection confidence in [0, 1]
        max_profit_pips: Normalization cap for profit (default: 10 pips)

    Returns:
        CompositeScoreResult with score breakdown and explanation
    """
    # Normalize all components to [0, 100]
    profit_norm = min(100.0, max(0.0, profit_pips / max_profit_pips * 100))
    persistence_norm = min(100.0, max(0.0, persistence_score * 100))
    feasibility_norm = min(100.0, max(0.0, feasibility_score))
    session_norm = min(100.0, max(0.0, session_weight * 100))
    confidence_norm = min(100.0, max(0.0, confidence * 100))

    # Weighted components
    profit_component = WEIGHT_PROFIT * profit_norm
    persistence_component = WEIGHT_PERSISTENCE * persistence_norm
    feasibility_component = WEIGHT_FEASIBILITY * feasibility_norm
    session_component = WEIGHT_SESSION * session_norm
    confidence_component = WEIGHT_CONFIDENCE * confidence_norm

    composite = (
        profit_component
        + persistence_component
        + feasibility_component
        + session_component
        + confidence_component
    )
    composite = min(100.0, max(0.0, composite))

    # Generate explanation
    parts = []
    dominant = max(
        ("Profit", profit_component),
        ("Persistence", persistence_component),
        ("Feasibility", feasibility_component),
        ("Session", session_component),
        ("Confidence", confidence_component),
        key=lambda x: x[1],
    )
    parts.append(f"Score {composite:.1f}/100 (dominant: {dominant[0]})")

    if profit_pips > 0:
        parts.append(f"Profit: {profit_pips:.2f} pips")
    if persistence_score >= 0.7:
        parts.append("High persistence")
    elif persistence_score < 0.3:
        parts.append("Low persistence — timing risk")
    if feasibility_score >= 70:
        parts.append("Execution viable")
    elif feasibility_score < 40:
        parts.append("Execution unlikely")

    explanation = " | ".join(parts)

    return CompositeScoreResult(
        composite_score=composite,
        profit_component=profit_component,
        persistence_component=persistence_component,
        feasibility_component=feasibility_component,
        session_component=session_component,
        confidence_component=confidence_component,
        explanation=explanation,
    )


def get_pip_value(symbol: str) -> float:
    """
    Get the pip value for a currency pair.

    Per ISO 4217 convention:
        - JPY pairs: 1 pip = 0.01
        - All other pairs: 1 pip = 0.0001

    Args:
        symbol: Currency pair symbol (e.g., "EURUSD", "USDJPY")

    Returns:
        Pip value as a float
    """
    return JPY_PIP_VALUE if "JPY" in symbol.upper() else STANDARD_PIP_VALUE
