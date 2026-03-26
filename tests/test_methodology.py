"""
Unit tests for the formalized arbitrage methodology module.

Tests cover:
    - Cross-source arbitrage condition detection
    - Net profit calculation with transaction costs
    - Triangular arbitrage detection and profit
    - Persistence classification thresholds
    - Composite score computation and weighting
"""

import pytest
from backend.core.arbitrage.methodology import (
    is_arbitrage,
    calculate_profit,
    is_triangular_arbitrage,
    calculate_triangular_profit,
    compute_persistence,
    compute_composite_score,
    get_pip_value,
    PersistenceClass,
    STANDARD_PIP_VALUE,
    JPY_PIP_VALUE,
    EPHEMERAL_THRESHOLD_MS,
    FLICKERING_THRESHOLD_MS,
)


# =============================================================================
# is_arbitrage
# =============================================================================

class TestIsArbitrage:
    """Tests for the cross-source arbitrage condition."""

    def test_arbitrage_exists_when_bid_exceeds_ask(self):
        """bid_A > ask_B → arbitrage exists."""
        assert is_arbitrage(bid_a=1.0852, ask_b=1.0850) is True

    def test_no_arbitrage_when_ask_exceeds_bid(self):
        """bid_A < ask_B → no arbitrage."""
        assert is_arbitrage(bid_a=1.0850, ask_b=1.0852) is False

    def test_no_arbitrage_when_equal(self):
        """bid_A == ask_B → no arbitrage (zero profit)."""
        assert is_arbitrage(bid_a=1.0850, ask_b=1.0850) is False

    def test_large_arbitrage_spread(self):
        """Large price discrepancy should be detected."""
        assert is_arbitrage(bid_a=1.0900, ask_b=1.0800) is True

    def test_tiny_arbitrage_spread(self):
        """Even a 0.00001 difference counts."""
        assert is_arbitrage(bid_a=1.08501, ask_b=1.08500) is True


# =============================================================================
# calculate_profit
# =============================================================================

class TestCalculateProfit:
    """Tests for net profit calculation."""

    def test_gross_profit_no_costs(self):
        """Without costs, profit is pure price differential in pips."""
        profit = calculate_profit(bid_a=1.0852, ask_b=1.0850)
        assert abs(profit - 2.0) < 0.01  # 2.0 pips

    def test_profit_with_spread_cost(self):
        """Spread costs reduce profit."""
        profit = calculate_profit(
            bid_a=1.0852, ask_b=1.0850, spread_cost_pips=0.5
        )
        assert abs(profit - 1.5) < 0.01

    def test_profit_with_all_costs(self):
        """All cost components subtract from gross profit."""
        profit = calculate_profit(
            bid_a=1.0852,
            ask_b=1.0850,
            spread_cost_pips=0.3,
            slippage_pips=0.2,
            latency_cost_pips=0.1,
        )
        assert abs(profit - 1.4) < 0.01  # 2.0 - 0.6

    def test_negative_profit_when_costs_exceed_gross(self):
        """Costs exceeding gross profit yield negative result."""
        profit = calculate_profit(
            bid_a=1.0851, ask_b=1.0850, spread_cost_pips=2.0
        )
        assert profit < 0

    def test_jpy_pair_pip_value(self):
        """JPY pairs use 0.01 pip value."""
        # 110.52 - 110.50 = 0.02 → 2 pips at 0.01 pip value
        profit = calculate_profit(
            bid_a=110.52, ask_b=110.50, pip_value=JPY_PIP_VALUE
        )
        assert abs(profit - 2.0) < 0.1

    def test_zero_profit_when_prices_equal(self):
        """Equal prices yield zero gross profit."""
        profit = calculate_profit(bid_a=1.0850, ask_b=1.0850)
        assert abs(profit) < 0.001


# =============================================================================
# Triangular Arbitrage
# =============================================================================

class TestTriangularArbitrage:
    """Tests for triangular arbitrage detection."""

    def test_no_arbitrage_at_unity(self):
        """Rate product = 1.0 → no arbitrage."""
        assert is_triangular_arbitrage(1.0, 1.0, 1.0) is False

    def test_arbitrage_above_threshold(self):
        """Significant deviation from unity → arbitrage."""
        # Product = 1.1 × 0.95 × 1.0 = 1.045 → deviation = 0.045
        assert is_triangular_arbitrage(1.1, 0.95, 1.0) is True

    def test_no_arbitrage_below_threshold(self):
        """Small deviation within threshold → no arbitrage."""
        # Product very close to 1
        assert is_triangular_arbitrage(1.0001, 0.9999, 1.0) is False

    def test_custom_threshold(self):
        """Custom threshold changes sensitivity."""
        # With tight threshold, even small deviation triggers
        assert is_triangular_arbitrage(
            1.001, 1.0, 1.0, threshold=0.0001
        ) is True

    def test_triangular_profit_positive(self):
        """Profitable cycle returns positive value."""
        # Product = 1.01 → 1% profit
        profit = calculate_triangular_profit(1.01, 1.0, 1.0, notional=100000)
        assert abs(profit - 1000.0) < 0.01  # $1000 on $100k

    def test_triangular_profit_negative(self):
        """Unprofitable cycle returns negative value."""
        profit = calculate_triangular_profit(0.99, 1.0, 1.0, notional=100000)
        assert profit < 0


# =============================================================================
# compute_persistence
# =============================================================================

class TestComputePersistence:
    """Tests for persistence classification."""

    def test_ephemeral_classification(self):
        """Duration < 50ms → EPHEMERAL."""
        result = compute_persistence(
            first_seen_ms=1000, last_seen_ms=1030, detection_count=1
        )
        assert result.persistence_class == PersistenceClass.EPHEMERAL
        assert result.duration_ms == 30

    def test_flickering_classification(self):
        """50ms ≤ duration < 300ms → FLICKERING."""
        result = compute_persistence(
            first_seen_ms=1000, last_seen_ms=1100, detection_count=2
        )
        assert result.persistence_class == PersistenceClass.FLICKERING

    def test_persistent_classification(self):
        """Duration ≥ 300ms with ≥3 detections → PERSISTENT."""
        result = compute_persistence(
            first_seen_ms=1000, last_seen_ms=1500, detection_count=5
        )
        assert result.persistence_class == PersistenceClass.PERSISTENT
        assert result.duration_ms == 500

    def test_long_duration_few_detections_is_flickering(self):
        """Duration ≥ 300ms but < 3 detections → FLICKERING (not persistent)."""
        result = compute_persistence(
            first_seen_ms=1000, last_seen_ms=1500, detection_count=2
        )
        assert result.persistence_class == PersistenceClass.FLICKERING

    def test_stability_score_range(self):
        """Stability score must be in [0, 1]."""
        result = compute_persistence(
            first_seen_ms=0, last_seen_ms=1000,
            detection_count=10, gap_count=2
        )
        assert 0.0 <= result.stability_score <= 1.0

    def test_stability_increases_with_detections(self):
        """More detections = higher stability."""
        few = compute_persistence(1000, 1500, detection_count=1)
        many = compute_persistence(1000, 1500, detection_count=10)
        assert many.stability_score > few.stability_score

    def test_zero_duration(self):
        """Same first and last seen → EPHEMERAL with 0 duration."""
        result = compute_persistence(first_seen_ms=1000, last_seen_ms=1000)
        assert result.persistence_class == PersistenceClass.EPHEMERAL
        assert result.duration_ms == 0


# =============================================================================
# compute_composite_score
# =============================================================================

class TestCompositeScore:
    """Tests for composite opportunity scoring."""

    def test_max_score(self):
        """Perfect inputs should approach 100."""
        result = compute_composite_score(
            profit_pips=10.0,
            persistence_score=1.0,
            feasibility_score=100.0,
            session_weight=1.0,
            confidence=1.0,
        )
        assert result.composite_score == pytest.approx(100.0, abs=0.1)

    def test_zero_score(self):
        """Zero inputs should give zero."""
        result = compute_composite_score(
            profit_pips=0.0,
            persistence_score=0.0,
            feasibility_score=0.0,
            session_weight=0.0,
            confidence=0.0,
        )
        assert result.composite_score == pytest.approx(0.0, abs=0.1)

    def test_score_increases_with_profit(self):
        """Higher profit → higher score."""
        low = compute_composite_score(0.5, 0.5, 50, 0.5, 0.5)
        high = compute_composite_score(5.0, 0.5, 50, 0.5, 0.5)
        assert high.composite_score > low.composite_score

    def test_score_increases_with_persistence(self):
        """Higher persistence → higher score."""
        low = compute_composite_score(1.0, 0.1, 50, 0.5, 0.5)
        high = compute_composite_score(1.0, 0.9, 50, 0.5, 0.5)
        assert high.composite_score > low.composite_score

    def test_explanation_is_nonempty(self):
        """Explanation string should always be populated."""
        result = compute_composite_score(1.0, 0.5, 60, 0.8, 0.7)
        assert len(result.explanation) > 0

    def test_components_sum_to_total(self):
        """Individual components should sum to composite score."""
        result = compute_composite_score(3.0, 0.6, 70, 0.8, 0.7)
        expected = (
            result.profit_component
            + result.persistence_component
            + result.feasibility_component
            + result.session_component
            + result.confidence_component
        )
        assert result.composite_score == pytest.approx(expected, abs=0.01)


# =============================================================================
# get_pip_value
# =============================================================================

class TestGetPipValue:
    """Tests for pip value determination."""

    def test_standard_pair(self):
        assert get_pip_value("EURUSD") == STANDARD_PIP_VALUE

    def test_jpy_pair(self):
        assert get_pip_value("USDJPY") == JPY_PIP_VALUE

    def test_case_insensitive(self):
        assert get_pip_value("usdjpy") == JPY_PIP_VALUE

    def test_inr_pair(self):
        assert get_pip_value("USDINR") == STANDARD_PIP_VALUE
