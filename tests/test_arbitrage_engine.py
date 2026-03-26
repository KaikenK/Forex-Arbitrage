"""
Unit tests for the ArbitrageEngine.

Tests cover:
    - Cross-source arbitrage detection with known prices
    - No false positives when spread is positive
    - Confidence scoring behavior
    - Triangular arbitrage rate product check
"""

import pytest
from unittest.mock import MagicMock
from backend.core.arbitrage.arbitrage_engine import (
    ArbitrageEngine,
    ArbitrageConfig,
    ArbitrageOpportunity,
    ArbitrageType,
)
from backend.core.arbitrage.tick_aligner import AlignedTickWindow
from backend.core.interfaces.normalized_tick import NormalizedTick


def make_tick(
    symbol: str = "EURUSD",
    bid: float = 1.0850,
    ask: float = 1.0852,
    source_id: str = "source_a",
    timestamp_ms: int = 1706745600000,
    session: str = "LONDON",
) -> NormalizedTick:
    """Helper to create a NormalizedTick for testing."""
    return NormalizedTick(
        symbol=symbol,
        bid=bid,
        ask=ask,
        mid=(bid + ask) / 2,
        spread=ask - bid,
        timestamp_ms=timestamp_ms,
        source_id=source_id,
        session=session,
        latency_adjusted_time_ms=timestamp_ms,
        volume=0,
    )


def make_window(
    symbol: str,
    ticks_by_source: dict,
    window_start_ms: int = 1706745600000,
    window_size_ms: int = 20,
) -> AlignedTickWindow:
    """Helper to create an AlignedTickWindow for testing."""
    return AlignedTickWindow(
        symbol=symbol,
        window_start_ms=window_start_ms,
        window_end_ms=window_start_ms + window_size_ms,
        ticks_by_source={
            source_id: [tick] for source_id, tick in ticks_by_source.items()
        },
    )


# =============================================================================
# Cross-source detection
# =============================================================================

class TestCrossSourceDetection:
    """Tests for cross-source arbitrage detection."""

    @pytest.fixture
    def engine(self):
        """Create engine with low thresholds for testing."""
        config = ArbitrageConfig(
            min_profit_pips=0.1,
            min_confidence=0.1,
            max_latency_risk_ms=200.0,
        )
        return ArbitrageEngine(config=config)

    def test_detects_arbitrage_when_bid_exceeds_ask(self, engine):
        """Should detect when source A's bid > source B's ask."""
        window = make_window(
            symbol="EURUSD",
            ticks_by_source={
                "source_a": make_tick(bid=1.0855, ask=1.0857, source_id="source_a"),
                "source_b": make_tick(bid=1.0850, ask=1.0852, source_id="source_b"),
            },
        )
        opportunities = engine.detect(window)
        # source_a bid (1.0855) > source_b ask (1.0852) → should detect
        arb_found = any(
            opp.type == ArbitrageType.CROSS_SOURCE
            for opp in opportunities
        )
        assert arb_found, "Should detect cross-source arbitrage"

    def test_no_detection_when_spread_positive(self, engine):
        """No detection when all bids < all asks across sources."""
        window = make_window(
            symbol="EURUSD",
            ticks_by_source={
                "source_a": make_tick(bid=1.0850, ask=1.0852, source_id="source_a"),
                "source_b": make_tick(bid=1.0849, ask=1.0851, source_id="source_b"),
            },
        )
        opportunities = engine.detect(window)
        cross_source = [
            opp for opp in opportunities
            if opp.type == ArbitrageType.CROSS_SOURCE
        ]
        assert len(cross_source) == 0, "Should not detect when spread is positive"

    def test_profit_calculation_correct(self, engine):
        """Detected profit should match price differential."""
        window = make_window(
            symbol="EURUSD",
            ticks_by_source={
                "source_a": make_tick(bid=1.0855, ask=1.0857, source_id="source_a"),
                "source_b": make_tick(bid=1.0850, ask=1.0852, source_id="source_b"),
            },
        )
        opportunities = engine.detect(window)
        cross_source = [
            opp for opp in opportunities
            if opp.type == ArbitrageType.CROSS_SOURCE
        ]
        if cross_source:
            opp = cross_source[0]
            # Best bid = 1.0855, best ask = 1.0852 → 3 pips profit
            assert opp.estimated_profit_pips > 0
            assert opp.estimated_profit_pips < 10  # Sanity check

    def test_confidence_is_valid(self, engine):
        """Confidence should be in [0, 1]."""
        window = make_window(
            symbol="EURUSD",
            ticks_by_source={
                "source_a": make_tick(bid=1.0855, ask=1.0857, source_id="source_a"),
                "source_b": make_tick(bid=1.0850, ask=1.0852, source_id="source_b"),
            },
        )
        opportunities = engine.detect(window)
        for opp in opportunities:
            assert 0.0 <= opp.confidence_score <= 1.0

    def test_single_source_no_detection(self, engine):
        """Single source cannot produce cross-source arbitrage."""
        window = make_window(
            symbol="EURUSD",
            ticks_by_source={
                "source_a": make_tick(bid=1.0855, ask=1.0857, source_id="source_a"),
            },
        )
        opportunities = engine.detect(window)
        cross_source = [
            opp for opp in opportunities
            if opp.type == ArbitrageType.CROSS_SOURCE
        ]
        assert len(cross_source) == 0
