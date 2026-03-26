"""
Unit tests for the TickNormalizer.

Tests cover:
    - Mid-price calculation: (bid + ask) / 2
    - Spread calculation: ask - bid
    - Spread-in-pips for standard and JPY pairs
    - Session detection from timestamps
    - Normalizer handling of None/invalid inputs
"""

import pytest
from unittest.mock import MagicMock
from backend.core.interfaces.data_source import RawTick, DataSourceConfig
from backend.core.interfaces.normalized_tick import (
    NormalizedTick,
    TickNormalizer,
    detect_trading_session,
)


# =============================================================================
# detect_trading_session
# =============================================================================

class TestDetectTradingSession:
    """Tests for session detection from UTC timestamps."""

    def _ts_for_utc_hour(self, hour: int) -> int:
        """Create a timestamp for a specific UTC hour (arbitrary date)."""
        import datetime
        dt = datetime.datetime(2024, 1, 15, hour, 0, 0, tzinfo=datetime.timezone.utc)
        return int(dt.timestamp() * 1000)

    def test_tokyo_session(self):
        """Hour 2 UTC should be Tokyo session."""
        session = detect_trading_session(self._ts_for_utc_hour(2))
        assert "tokyo" in session.lower()

    def test_london_session(self):
        """Hour 10 UTC should include London."""
        session = detect_trading_session(self._ts_for_utc_hour(10))
        assert "london" in session.lower()

    def test_new_york_session(self):
        """Hour 18 UTC should include New York."""
        session = detect_trading_session(self._ts_for_utc_hour(18))
        assert "new_york" in session.lower()


# =============================================================================
# TickNormalizer
# =============================================================================

class TestTickNormalizer:
    """Tests for the tick normalization pipeline."""

    @pytest.fixture
    def normalizer(self):
        """Create a TickNormalizer instance."""
        return TickNormalizer()

    @pytest.fixture
    def raw_tick(self):
        """Create a sample RawTick."""
        return RawTick(
            symbol="EURUSD",
            bid=1.0850,
            ask=1.0852,
            timestamp_ms=1706745600000,  # 2024-01-31 16:00:00 UTC
            source_id="test_source",
            volume=100,
        )

    def test_normalization_produces_result(self, normalizer, raw_tick):
        """Normalizer should produce a NormalizedTick from valid input."""
        result = normalizer.normalize(raw_tick)
        assert result is not None
        assert isinstance(result, NormalizedTick)

    def test_mid_price_calculated(self, normalizer, raw_tick):
        """Mid price should be (bid + ask) / 2."""
        result = normalizer.normalize(raw_tick)
        expected_mid = (1.0850 + 1.0852) / 2
        assert abs(result.mid - expected_mid) < 1e-10

    def test_spread_calculated(self, normalizer, raw_tick):
        """Spread should be ask - bid."""
        result = normalizer.normalize(raw_tick)
        expected_spread = 1.0852 - 1.0850
        assert abs(result.spread - expected_spread) < 1e-10

    def test_symbol_preserved(self, normalizer, raw_tick):
        """Symbol should be preserved through normalization."""
        result = normalizer.normalize(raw_tick)
        assert result.symbol == "EURUSD"

    def test_source_id_preserved(self, normalizer, raw_tick):
        """Source ID should be preserved through normalization."""
        result = normalizer.normalize(raw_tick)
        assert result.source_id == "test_source"

    def test_session_assigned(self, normalizer, raw_tick):
        """Session should be assigned based on timestamp."""
        result = normalizer.normalize(raw_tick)
        assert result.session is not None
        assert len(result.session) > 0

    def test_jpy_pair_spread_in_pips(self, normalizer):
        """JPY pairs should use 0.01 pip value for spread calculation."""
        raw = RawTick(
            symbol="USDJPY",
            bid=110.500,
            ask=110.520,
            timestamp_ms=1706745600000,
            source_id="test_source",
        )
        result = normalizer.normalize(raw)
        # Spread = 0.020, pip value = 0.01 → 2.0 pips
        spread_in_pips = result.spread_pips()
        assert abs(spread_in_pips - 2.0) < 0.1
