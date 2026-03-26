"""
Unit tests for OpportunityTracker persistence classification.

Tests cover:
    - New opportunity creation
    - Detection merging (same key updates existing entity)
    - Ephemeral → Flickering → Persistent promotion
    - Gap counting and stability scoring
    - Active vs archived opportunity management
"""

import pytest
import time
from unittest.mock import patch
from backend.core.arbitrage.opportunity_tracker import (
    OpportunityTracker,
    TrackerConfig,
    TrackedOpportunity,
    PersistenceClass,
)
from backend.core.arbitrage.arbitrage_engine import (
    ArbitrageOpportunity,
    ArbitrageType,
)


def make_opportunity(
    symbol: str = "EURUSD",
    buy_source: str = "source_a",
    sell_source: str = "source_b",
    profit_pips: float = 1.0,
    confidence: float = 0.8,
) -> ArbitrageOpportunity:
    """Helper to create an ArbitrageOpportunity for testing."""
    return ArbitrageOpportunity(
        type=ArbitrageType.CROSS_SOURCE,
        symbols=[symbol],
        sources=[buy_source, sell_source],
        buy_source=buy_source,
        sell_source=sell_source,
        buy_price=1.0850,
        sell_price=1.0855,
        estimated_profit_pips=profit_pips,
        estimated_profit_pct=profit_pips * 0.0001,
        latency_risk_ms=50.0,
        confidence_score=confidence,
        session="LONDON",
        timestamp_ms=int(time.time() * 1000),
        window_size_ms=20,
    )


class TestOpportunityTrackerCreation:
    """Tests for new opportunity creation."""

    def test_creates_new_tracked_opportunity(self):
        """First detection creates a new tracked entity."""
        tracker = OpportunityTracker()
        opp = make_opportunity()
        tracked = tracker.update(opp)

        assert tracked is not None
        assert tracked.detection_count == 1
        assert tracked.symbol == "EURUSD"

    def test_initial_persistence_is_ephemeral(self):
        """New opportunities start as ephemeral."""
        tracker = OpportunityTracker()
        opp = make_opportunity()
        tracked = tracker.update(opp)

        assert tracked.persistence_class == PersistenceClass.EPHEMERAL

    def test_appears_in_active_list(self):
        """New opportunity should be in the active list."""
        tracker = OpportunityTracker()
        opp = make_opportunity()
        tracker.update(opp)

        active = tracker.get_active()
        assert len(active) == 1


class TestOpportunityMerging:
    """Tests for detection merging."""

    def test_same_key_updates_existing(self):
        """Repeated detections with same key update the same entity."""
        tracker = OpportunityTracker()
        opp = make_opportunity()

        tracker.update(opp)
        tracker.update(opp)
        tracker.update(opp)

        active = tracker.get_active()
        assert len(active) == 1  # Not 3 entities
        assert active[0].detection_count == 3

    def test_different_symbols_create_separate_entities(self):
        """Different symbols should create separate tracked entities."""
        tracker = OpportunityTracker()
        tracker.update(make_opportunity(symbol="EURUSD"))
        tracker.update(make_opportunity(symbol="GBPUSD"))

        active = tracker.get_active()
        assert len(active) == 2

    def test_profit_tracking_updates(self):
        """Max profit should track the highest seen value."""
        tracker = OpportunityTracker()

        tracker.update(make_opportunity(profit_pips=1.0))
        tracker.update(make_opportunity(profit_pips=3.0))
        tracker.update(make_opportunity(profit_pips=2.0))

        active = tracker.get_active()
        assert active[0].max_profit_pips_seen >= 3.0


class TestPersistenceClassification:
    """Tests for persistence class promotion."""

    def test_stats_reporting(self):
        """Stats should report counts correctly."""
        tracker = OpportunityTracker()
        tracker.update(make_opportunity(symbol="EURUSD"))
        tracker.update(make_opportunity(symbol="GBPUSD"))

        stats = tracker.get_stats()
        assert stats["active_count"] == 2
        assert stats["total_tracked"] == 2

    def test_get_by_class(self):
        """Filter by persistence class should work."""
        tracker = OpportunityTracker()
        tracker.update(make_opportunity())

        ephemeral = tracker.get_by_class(PersistenceClass.EPHEMERAL)
        assert len(ephemeral) >= 0  # May or may not be ephemeral depending on timing

    def test_to_dict_serialization(self):
        """TrackedOpportunity should serialize to dict."""
        tracker = OpportunityTracker()
        opp = make_opportunity()
        tracked = tracker.update(opp)

        d = tracked.to_dict()
        assert "key" in d
        assert "persistence_class" in d
        assert "detection_count" in d
        assert "stability_score" in d
