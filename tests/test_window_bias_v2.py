"""Window bias v2 (C35, shadow): dedup, publisher time, release items, pair orientation."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.core.currency_bias_channels import channel_snapshot
from backend.core.window_bias_v2 import orientation, window_bias_v2

NOW = datetime(2026, 11, 5, 14, 0, tzinfo=UTC)


def _record(headline, *, produced_min, published_min=None, sentiment=0.0, confidence=0.5, score=None):
    feed = {"headline": headline, "source": "RSS (test)"}
    if published_min is not None:
        feed["publisher_time"] = (NOW + timedelta(minutes=published_min)).isoformat()
    return {"produced_at": (NOW + timedelta(minutes=produced_min)).isoformat(), "sentiment_score": sentiment,
            "confidence": confidence, "feed": feed, "pair_score": score or {"basis": "news"}}


def test_orientation():
    assert orientation("EUR/USD", ["USD"]) == -1 and orientation("USD/JPY", ["USD"]) == 1
    assert orientation("EUR/USD", ["EUR", "USD"]) == 0 and orientation("EUR/USD", []) == 0


def test_news_items_are_oriented_and_deduplicated():
    history = [
        _record("Dollar slides as Fed signals cuts", produced_min=-10, published_min=-12, sentiment=-0.6),
        _record("Dollar slides as Fed signals cuts", produced_min=-2, sentiment=-0.6),     # re-analysed copy
        _record("Stocks rally on earnings", produced_min=-5, sentiment=0.9),             # no currency: activity
    ]
    eur = window_bias_v2(history, "EUR/USD", NOW)
    assert eur["item_count"] == 2 and eur["duplicates_dropped"] == 1 and eur["unoriented_items"] == 1
    assert eur["market_bias"] == pytest.approx(0.6)               # dollar weaker -> EUR/USD up
    assert window_bias_v2(history, "USD/JPY", NOW)["market_bias"] == pytest.approx(-0.6)
    assert eur["activity_factor"] == pytest.approx(2 / 5) and eur["semantic_score"] == pytest.approx(0.24)


def test_publisher_time_keeps_late_arrivals_out_and_distrusts_future_clocks():
    history = [
        _record("Euro jumps after ECB surprise", produced_min=-1, published_min=-60, sentiment=0.8),  # an hour late
        _record("Euro firms as ECB holds", produced_min=-3, published_min=+30, sentiment=0.5),        # clock ahead
    ]
    out = window_bias_v2(history, "EUR/USD", NOW)
    assert out["late_dropped"] == 1 and out["item_count"] == 1 and out["market_bias"] == pytest.approx(0.5)
    # Evaluated before an item was analysed (a replay), it is not visible yet.
    replay = window_bias_v2([_record("Euro rises as ECB signals hike", produced_min=+20, published_min=-5,
                                     sentiment=0.4)], "EUR/USD", NOW)
    assert replay["item_count"] == 0


def test_release_item_carries_calibrated_direction_and_expected_move():
    release = {"basis": "release", "release_direction": -1, "p_direction": 0.67, "expected_move_bps": 9.3}
    history = [_record("US ISM manufacturing PMI 55.5 vs 54.5", produced_min=-20, published_min=-29, score=release),
               _record("Euro rises as ECB signals hike", produced_min=-1, published_min=-1, sentiment=0.4,
                       confidence=0.5)]
    out = window_bias_v2(history, "EUR/USD", NOW)
    r_release, r_news = 0.1, 1 - 1 / 30                            # 29 min old: the 0.1 recency floor
    w_release, w_news = r_release * 0.67, r_news * 0.5
    assert out["release_items"] == 1 and out["news_items"] == 1 and out["expected_move_bps"] == pytest.approx(9.3)
    assert out["market_bias"] == pytest.approx((w_release * -1 + w_news * 0.4) / (w_release + w_news), abs=1e-4)
    empty = window_bias_v2([], "EUR/USD", NOW)
    assert empty["item_count"] == 0 and empty["market_bias"] == 0.0 and empty["expected_move_bps"] is None


def test_v2_goes_through_the_live_penalty_formula_and_into_the_snapshot():
    from backend.core.semantic_engine import LiveNewsBiasClient

    history = [_record(f"Dollar weakens on soft data {i}", produced_min=-i, sentiment=-0.9, confidence=0.8)
               for i in range(1, 7)]
    payload = window_bias_v2(history, "EUR/USD", NOW)
    client = LiveNewsBiasClient(bridge=None, enabled=True, clock=lambda: NOW.timestamp())
    bias = client._to_bias(pair="EUR/USD", payload=payload, freshness_seconds=0.0).to_dict()
    assert bias["semantic_score"] == pytest.approx(0.9) and bias["confidence_adjustment"] == pytest.approx(-0.072)
    snap = channel_snapshot("EUR/USD", "cross_source", window_bias={"semantic_score": 0.0}, client=None,
                            applied=False, window_bias_v2=bias)
    assert snap["window_bias_v2"]["residual_exposure"] == 1 and snap["window_bias"] == {"semantic_score": 0.0}
    assert channel_snapshot("EUR/USD", "cross_source", window_bias=None, client=None, applied=False)["window_bias_v2"] is None
