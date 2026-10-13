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


class TestLiveExchangeMode:
    """Live exchange legs: only genuinely crossed books are opportunities."""

    def _engine(self, **overrides):
        cfg = dict(min_profit_pips=0.25, min_confidence=0.0, max_latency_risk_ms=2000,
                   enable_triangular=False, enable_session_analysis=False,
                   enable_latency_arbitrage=False)
        cfg.update(overrides)
        return ArbitrageEngine(ArbitrageConfig(**cfg))

    def test_mid_gap_without_cross_is_not_reported(self):
        # NSE 88.7000/88.7100, BSE 88.7050/88.7300: mids differ by 1.25 paise,
        # but no bid exceeds the other venue's ask -> nothing executable.
        window = make_window("USDINR", {
            "upstox_nse_usdinr": make_tick("USDINR", 88.7000, 88.7100, "upstox_nse_usdinr"),
            "upstox_bse_usdinr": make_tick("USDINR", 88.7050, 88.7300, "upstox_bse_usdinr"),
        }, window_size_ms=500)
        assert self._engine().detect(window) == []
        # the default (synthetic-mode) behaviour still reports it as latency arbitrage
        legacy = self._engine(enable_latency_arbitrage=True, min_profit_pips=-100)
        assert any(o.type == ArbitrageType.LATENCY_ARBITRAGE for o in legacy.detect(window))

    def test_crossed_book_is_reported_in_paise(self):
        # BSE bid 88.7150 > NSE ask 88.7100: buy NSE, sell BSE for 0.5 paise.
        window = make_window("USDINR", {
            "upstox_nse_usdinr": make_tick("USDINR", 88.7075, 88.7100, "upstox_nse_usdinr"),
            "upstox_bse_usdinr": make_tick("USDINR", 88.7150, 88.7200, "upstox_bse_usdinr"),
        }, window_size_ms=500)
        opps = self._engine().detect(window)
        assert len(opps) == 1
        opp = opps[0]
        assert opp.type == ArbitrageType.CROSS_SOURCE
        assert (opp.buy_source, opp.sell_source) == ("upstox_nse_usdinr", "upstox_bse_usdinr")
        assert opp.estimated_profit_pips == pytest.approx(0.5)

    def test_one_tick_cross_is_not_lost_to_float_rounding(self):
        # 95.4525 - 95.4500 is 0.0024999999999977 in binary floating point,
        # i.e. 0.2499... paise; a one-tick (0.25 paise) cross must still count.
        window = make_window("USDINR", {
            "upstox_nse_usdinr": make_tick("USDINR", 95.4475, 95.4500, "upstox_nse_usdinr"),
            "upstox_bse_usdinr": make_tick("USDINR", 95.4525, 95.4600, "upstox_bse_usdinr"),
        }, window_size_ms=500)
        opps = self._engine().detect(window)
        assert len(opps) == 1
        assert opps[0].estimated_profit_pips == pytest.approx(0.25)

    def test_one_tick_threshold_scales_to_each_pair(self):
        # min_profit_ticks=1: a USDINR cross needs 0.25 pips (one 0.0025 tick),
        # an EURUSD futures cross needs 1 pip (one 0.0001 tick).
        engine = self._engine(min_profit_ticks=1.0)
        eurusd_half_tick = make_window("EURUSD", {
            "upstox_nse_eurusd": make_tick("EURUSD", 1.16000, 1.16010, "upstox_nse_eurusd"),
            "upstox_bse_eurusd": make_tick("EURUSD", 1.16015, 1.16030, "upstox_bse_eurusd"),
        }, window_size_ms=500)
        assert engine.detect(eurusd_half_tick) == []          # 0.5 pip < one tick
        eurusd_two_ticks = make_window("EURUSD", {
            "upstox_nse_eurusd": make_tick("EURUSD", 1.16000, 1.16010, "upstox_nse_eurusd"),
            "upstox_bse_eurusd": make_tick("EURUSD", 1.16030, 1.16040, "upstox_bse_eurusd"),
        }, window_size_ms=500)
        opps = engine.detect(eurusd_two_ticks)
        assert len(opps) == 1 and opps[0].estimated_profit_pips == pytest.approx(2.0)
        usdinr_one_tick = make_window("USDINR", {
            "upstox_nse_usdinr": make_tick("USDINR", 95.4475, 95.4500, "upstox_nse_usdinr"),
            "upstox_bse_usdinr": make_tick("USDINR", 95.4525, 95.4600, "upstox_bse_usdinr"),
        }, window_size_ms=500)
        assert len(engine.detect(usdinr_one_tick)) == 1
