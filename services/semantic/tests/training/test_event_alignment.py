from __future__ import annotations

import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from training.labels.event_alignment import EventAlignment
from training.models import PriceBar


def test_event_alignment_uses_future_windows_only() -> None:
    alignment = EventAlignment()
    event_timestamp = datetime(2026, 3, 25, 12, 0, 30, tzinfo=UTC)
    price_bars = [
        PriceBar(symbol="EUR/USD", timestamp=datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC), close=1.1000),
        PriceBar(symbol="EUR/USD", timestamp=datetime(2026, 3, 25, 12, 1, 0, tzinfo=UTC), close=1.1005),
        PriceBar(symbol="EUR/USD", timestamp=datetime(2026, 3, 25, 12, 6, 0, tzinfo=UTC), close=1.1015),
    ]

    aligned_window = alignment.align(event_timestamp, price_bars, horizon_minutes=5)

    assert aligned_window is not None
    assert aligned_window.start_bar.timestamp == datetime(2026, 3, 25, 12, 1, 0, tzinfo=UTC)
    assert aligned_window.end_bar.timestamp == datetime(2026, 3, 25, 12, 6, 0, tzinfo=UTC)


def test_event_alignment_returns_none_when_future_window_is_missing() -> None:
    alignment = EventAlignment()
    event_timestamp = datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)
    price_bars = [
        PriceBar(symbol="EUR/USD", timestamp=datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC), close=1.1000),
        PriceBar(symbol="EUR/USD", timestamp=datetime(2026, 3, 25, 12, 3, 0, tzinfo=UTC), close=1.1004),
    ]

    assert alignment.align(event_timestamp, price_bars, horizon_minutes=5) is None


def test_event_alignment_rejects_horizons_finer_than_bar_spacing() -> None:
    alignment = EventAlignment()
    event_timestamp = datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)
    price_bars = [
        PriceBar(symbol="USD/INR", timestamp=datetime(2026, 3, 25, 0, 0, 0, tzinfo=UTC), close=83.1000),
        PriceBar(symbol="USD/INR", timestamp=datetime(2026, 3, 26, 0, 0, 0, tzinfo=UTC), close=83.2500),
        PriceBar(symbol="USD/INR", timestamp=datetime(2026, 3, 27, 0, 0, 0, tzinfo=UTC), close=83.4000),
    ]

    assert alignment.align(event_timestamp, price_bars, horizon_minutes=60) is None
    assert alignment.align(event_timestamp, price_bars, horizon_minutes=240) is None


def test_event_alignment_rejects_windows_when_start_bar_is_too_late() -> None:
    alignment = EventAlignment()
    event_timestamp = datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)
    price_bars = [
        PriceBar(symbol="USD/INR", timestamp=datetime(2026, 3, 25, 12, 10, 0, tzinfo=UTC), close=83.10),
        PriceBar(symbol="USD/INR", timestamp=datetime(2026, 3, 25, 12, 15, 0, tzinfo=UTC), close=83.18),
    ]

    assert alignment.align(event_timestamp, price_bars, horizon_minutes=5) is None


def test_event_alignment_rejects_windows_when_end_bar_misses_target_by_a_gap() -> None:
    alignment = EventAlignment()
    event_timestamp = datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)
    price_bars = [
        PriceBar(symbol="USD/INR", timestamp=datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC), close=83.10),
        PriceBar(symbol="USD/INR", timestamp=datetime(2026, 3, 25, 12, 5, 0, tzinfo=UTC), close=83.14),
        PriceBar(symbol="USD/INR", timestamp=datetime(2026, 3, 25, 12, 20, 0, tzinfo=UTC), close=83.30),
    ]

    assert alignment.align(event_timestamp, price_bars, horizon_minutes=10) is None
