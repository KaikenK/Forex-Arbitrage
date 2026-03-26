"""
Integration / simulation test for the arbitrage detection pipeline.

Tests cover:
    - Synthetic data source creation and connection
    - Tick normalization pipeline
    - End-to-end detection with known arbitrage-producing prices
    - MetricsCollector recording
"""

import pytest
from backend.core.interfaces.data_source import DataSourceConfig, RawTick
from backend.core.interfaces.normalized_tick import TickNormalizer, NormalizedTick
from backend.core.arbitrage.arbitrage_engine import ArbitrageEngine, ArbitrageConfig
from backend.core.arbitrage.tick_aligner import AlignedTickWindow
from backend.core.analytics.metrics_collector import MetricsCollector
from backend.core.arbitrage.methodology import is_arbitrage, calculate_profit


class TestNormalizationPipeline:
    """Test the tick normalization pipeline end-to-end."""

    @pytest.fixture
    def normalizer(self):
        return TickNormalizer()

    def test_normalize_standard_tick(self, normalizer):
        """Standard tick should normalize without errors."""
        raw = RawTick(
            symbol="USDINR",
            bid=83.4200,
            ask=83.4250,
            timestamp_ms=1706745600000,
            source_id="bloomberg_tokyo",
            volume=1000,
        )
        result = normalizer.normalize(raw)
        assert result is not None
        assert result.symbol == "USDINR"
        assert result.bid == 83.4200
        assert result.ask == 83.4250

    def test_normalize_multiple_sources(self, normalizer):
        """Ticks from different sources should normalize independently."""
        ticks = [
            RawTick("USDINR", 83.4200, 83.4250, 1706745600000, "bloomberg", 100),
            RawTick("USDINR", 83.4210, 83.4260, 1706745600010, "reuters", 100),
        ]
        results = [normalizer.normalize(t) for t in ticks]
        assert all(r is not None for r in results)
        assert results[0].source_id == "bloomberg"
        assert results[1].source_id == "reuters"


class TestDetectionPipeline:
    """Test the detection pipeline with known prices."""

    @pytest.fixture
    def engine(self):
        return ArbitrageEngine(config=ArbitrageConfig(
            min_profit_pips=0.1,
            min_confidence=0.1,
            max_latency_risk_ms=200.0,
        ))

    def test_detects_from_divergent_prices(self, engine):
        """Two sources with divergent prices should produce detection."""
        # Bloomberg bids 83.4300, Reuters asks 83.4250
        # → bid > ask across sources = arbitrage
        bloomberg_tick = NormalizedTick(
            symbol="USDINR", bid=83.4300, ask=83.4350,
            mid=83.4325, spread=0.0050,
            timestamp_ms=1706745600000, source_id="bloomberg",
            session="LONDON", latency_adjusted_time_ms=1706745600000,
        )
        reuters_tick = NormalizedTick(
            symbol="USDINR", bid=83.4200, ask=83.4250,
            mid=83.4225, spread=0.0050,
            timestamp_ms=1706745600000, source_id="reuters",
            session="LONDON", latency_adjusted_time_ms=1706745600000,
        )

        window = AlignedTickWindow(
            symbol="USDINR",
            window_start_ms=1706745600000,
            window_end_ms=1706745600020,
            ticks_by_source={
                "bloomberg": [bloomberg_tick],
                "reuters": [reuters_tick],
            },
        )

        opportunities = engine.detect(window)
        assert len(opportunities) > 0, "Should detect arbitrage from divergent prices"

    def test_no_detection_from_aligned_prices(self, engine):
        """Two sources with nearly identical prices should not detect."""
        tick_a = NormalizedTick(
            symbol="USDINR", bid=83.4200, ask=83.4250,
            mid=83.4225, spread=0.0050,
            timestamp_ms=1706745600000, source_id="bloomberg",
            session="LONDON", latency_adjusted_time_ms=1706745600000,
        )
        tick_b = NormalizedTick(
            symbol="USDINR", bid=83.4200, ask=83.4250,
            mid=83.4225, spread=0.0050,
            timestamp_ms=1706745600000, source_id="reuters",
            session="LONDON", latency_adjusted_time_ms=1706745600000,
        )

        window = AlignedTickWindow(
            symbol="USDINR",
            window_start_ms=1706745600000,
            window_end_ms=1706745600020,
            ticks_by_source={
                "bloomberg": [tick_a],
                "reuters": [tick_b],
            },
        )

        opportunities = engine.detect(window)
        cross_src = [o for o in opportunities if o.type.value == "cross_source"]
        assert len(cross_src) == 0, "Should not detect from aligned prices"


class TestMetricsCollectorIntegration:
    """Test the MetricsCollector with simulated pipeline output."""

    def test_records_opportunities(self):
        """Collector should accumulate opportunity stats."""
        collector = MetricsCollector()
        collector.record_opportunity(
            session="LONDON",
            duration_ms=200,
            profit_pips=0.5,
            persistence_class="flickering",
        )
        collector.record_opportunity(
            session="TOKYO",
            duration_ms=500,
            profit_pips=1.2,
            persistence_class="persistent",
        )

        summary = collector.get_summary()
        assert summary["total_opportunities_detected"] == 2
        assert summary["persistence_distribution"]["flickering"] == 1
        assert summary["persistence_distribution"]["persistent"] == 1
        assert "LONDON" in summary["session_metrics"]
        assert "TOKYO" in summary["session_metrics"]

    def test_records_ticks(self):
        """Collector should accumulate tick/spread stats."""
        collector = MetricsCollector()
        collector.record_tick("LONDON", spread_pips=1.5)
        collector.record_tick("LONDON", spread_pips=1.7)
        collector.record_tick("TOKYO", spread_pips=2.0)

        summary = collector.get_summary()
        assert summary["total_ticks_processed"] == 3
        london = summary["session_metrics"]["LONDON"]
        assert london["tick_count"] == 2
        assert london["avg_spread_pips"] > 0

    def test_reset_clears_state(self):
        """Reset should zero all counters."""
        collector = MetricsCollector()
        collector.record_opportunity("LONDON", 100, 0.5, "ephemeral")
        collector.reset()

        summary = collector.get_summary()
        assert summary["total_opportunities_detected"] == 0
        assert len(summary["session_metrics"]) == 0


class TestMethodologyWithRealPrices:
    """Test methodology functions with realistic FX prices."""

    def test_usdinr_arbitrage_detection(self):
        """Detect arbitrage with realistic USD/INR prices."""
        # Bloomberg bid higher than Reuters ask
        assert is_arbitrage(bid_a=83.4300, ask_b=83.4250) is True

    def test_usdinr_profit_with_costs(self):
        """Calculate profit with realistic USD/INR costs."""
        profit = calculate_profit(
            bid_a=83.4300,
            ask_b=83.4250,
            spread_cost_pips=1.0,
            slippage_pips=0.5,
            latency_cost_pips=0.3,
            pip_value=0.0001,
        )
        # Gross: (83.4300 - 83.4250) / 0.0001 = 50 pips
        # Net: 50 - 1.0 - 0.5 - 0.3 = 48.2 pips
        assert profit > 0

    def test_no_arbitrage_normal_market(self):
        """Normal market conditions should show no arbitrage."""
        # Both sources quote nearly identical prices
        assert is_arbitrage(bid_a=83.4200, ask_b=83.4250) is False
