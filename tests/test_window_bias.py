"""Regression tests for the reaction-model-driven news window bias.

Before this, both writers into the 30-minute window (news_stream's serve.py SSE
handler and its BackgroundNewsPoller) recorded *raw FinBERT headline sentiment*,
and LiveNewsBiasClient consumed that aggregate. FinBERT is trained on equity and
corporate text, so for USD/INR it reads "rupee plunges" as strongly negative --
while the tradable implication is that the pair RISES. The bias that reached
arbitrage was therefore directionally inverted for the one pair the system is
built around.

SentimentBridge.get_window_bias aggregates the locally mirrored analyses instead,
using the reaction model's market_score as the directional term. The model was
trained on realised USD/INR returns, so it already encodes the inversion.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from backend.core.semantic_engine import LiveNewsBiasClient
from backend.core.sentiment_bridge import SentimentBridge


PAIR = "USD/INR"


def _analysis(*, sentiment: float, market: float, confidence: float, age_minutes: int) -> dict:
    produced_at = datetime.now(UTC) - timedelta(minutes=age_minutes)
    return {
        "event_id": f"evt-{age_minutes}-{market}",
        "sentiment_score": sentiment,
        "market_score": market,
        "confidence": confidence,
        "trading_signal": "BUY" if market > 0 else "SELL" if market < 0 else "HOLD",
        "produced_at": produced_at.isoformat(),
        "feed": {"headline": f"headline {age_minutes}", "source": "RSS"},
    }


def _bridge_with(history: list[dict], tmp_path) -> SentimentBridge:
    # enabled=False keeps the bridge entirely offline: no upstream call can occur.
    bridge = SentimentBridge(enabled=False)
    bridge._runtime_history_root = tmp_path
    bridge._runtime_cache_loaded.add(PAIR)
    bridge._analysis_history[PAIR] = history
    return bridge


def _bias(history: list[dict], tmp_path):
    bridge = _bridge_with(history, tmp_path)
    client = LiveNewsBiasClient(bridge=bridge, window_minutes=30)
    return bridge.get_window_bias(PAIR, 30), asyncio.run(client.get_bias("USDINR"))


# --------------------------------------------------------------------------
# The inversion: this is the whole point of the change.
# --------------------------------------------------------------------------

def test_bearish_inr_headlines_produce_risk_on_for_usdinr(tmp_path):
    """Strongly negative headline sentiment, positive predicted move => risk_on.

    Mirrors captured articles such as "Trump threatens 25% tariffs on all Indian
    imports, rupee plunges 1.2%": FinBERT -0.87, reaction model +0.00082.
    """
    history = [
        _analysis(sentiment=-0.87, market=0.00082, confidence=0.62, age_minutes=1),
        _analysis(sentiment=-0.64, market=0.00081, confidence=0.55, age_minutes=2),
        _analysis(sentiment=-0.58, market=0.00082, confidence=0.59, age_minutes=3),
        _analysis(sentiment=-0.87, market=0.00082, confidence=0.62, age_minutes=4),
        _analysis(sentiment=-0.64, market=0.00081, confidence=0.55, age_minutes=5),
    ]
    payload, bias = _bias(history, tmp_path)

    assert payload["overall_sentiment"] < -0.5, "headline sentiment must stay bearish"
    assert payload["market_bias"] > 0, "reaction model must stay bullish on the pair"
    # The old code derived direction from overall_sentiment and returned risk_off.
    assert bias.direction == "risk_on"
    assert bias.semantic_score > 0


def test_bearish_usdinr_still_produces_risk_off(tmp_path):
    """A genuinely bearish predicted move must remain risk_off."""
    history = [
        _analysis(sentiment=-0.94, market=-0.00079, confidence=0.62, age_minutes=1),
        _analysis(sentiment=-0.96, market=-0.00092, confidence=0.59, age_minutes=2),
        _analysis(sentiment=0.20, market=-0.00085, confidence=0.55, age_minutes=3),
    ]
    _, bias = _bias(history, tmp_path)
    assert bias.direction == "risk_off"


def test_negative_headlines_with_flat_model_are_neutral(tmp_path):
    """No predicted move => no bias, however alarming the headlines read.

    The old sentiment-driven path penalised arbitrage confidence here purely
    because the wire copy was negative.
    """
    history = [
        _analysis(sentiment=-0.81, market=0.0, confidence=0.58, age_minutes=1),
        _analysis(sentiment=-0.77, market=0.0, confidence=0.55, age_minutes=2),
        _analysis(sentiment=-0.90, market=0.0, confidence=0.61, age_minutes=3),
    ]
    _, bias = _bias(history, tmp_path)
    assert bias.direction == "neutral"
    assert bias.confidence_adjustment == 0.0


# --------------------------------------------------------------------------
# Aggregation properties
# --------------------------------------------------------------------------

def test_window_excludes_stale_analyses(tmp_path):
    history = [
        _analysis(sentiment=-0.5, market=0.0008, confidence=0.6, age_minutes=5),
        _analysis(sentiment=-0.5, market=0.0008, confidence=0.6, age_minutes=45),
    ]
    payload, _ = _bias(history, tmp_path)
    assert payload["item_count"] == 1, "the 45-minute-old item is outside a 30m window"


def test_empty_window_is_neutral_not_an_error(tmp_path):
    payload, bias = _bias([], tmp_path)
    assert payload["item_count"] == 0
    assert payload["market_bias"] == 0.0
    assert bias.confidence_adjustment == 0.0


def test_confidence_is_not_double_counted(tmp_path):
    """Uniform low confidence must not shrink the directional score.

    get_window_bias already weights by confidence and divides by total weight, so
    the result is a confidence-weighted *mean*. Scaling it by mean confidence a
    second time in _to_bias would damp every reading toward zero.
    """
    high = [_analysis(sentiment=-0.8, market=0.0009, confidence=0.9, age_minutes=i) for i in range(1, 6)]
    low = [_analysis(sentiment=-0.8, market=0.0009, confidence=0.3, age_minutes=i) for i in range(1, 6)]

    high_payload, high_bias = _bias(high, tmp_path)
    low_payload, low_bias = _bias(low, tmp_path)

    assert high_payload["market_bias"] == low_payload["market_bias"]
    assert high_bias.semantic_score == low_bias.semantic_score


def test_bias_stays_within_the_documented_cap(tmp_path):
    """News may never move arbitrage confidence by more than 8 points."""
    history = [
        _analysis(sentiment=-1.0, market=0.005, confidence=1.0, age_minutes=i)
        for i in range(1, 11)
    ]
    _, bias = _bias(history, tmp_path)
    assert abs(bias.confidence_adjustment) <= 0.08
    assert bias.confidence_adjustment <= 0.0, "news may reduce confidence, never raise it"


def test_conflicting_window_is_flagged_conflicted(tmp_path):
    history = [
        _analysis(sentiment=-0.5, market=0.0009, confidence=0.6, age_minutes=1),
        _analysis(sentiment=-0.5, market=0.0009, confidence=0.6, age_minutes=2),
        _analysis(sentiment=0.5, market=-0.0009, confidence=0.6, age_minutes=3),
        _analysis(sentiment=0.5, market=-0.0009, confidence=0.6, age_minutes=4),
    ]
    payload, bias = _bias(history, tmp_path)
    assert payload["bullish_count"] == 2
    assert payload["bearish_count"] == 2
    assert bias.regime == "conflicted"


def test_panel_direction_matches_engine_direction(tmp_path):
    """The dashboard and the engine must never disagree about direction.

    The panel derived its chip from the raw market_bias while the engine acted on
    the activity-damped score, so a 2-article window could read RISK OFF on screen
    (-0.161) while arbitrage applied NEUTRAL (-0.161 x 0.4 = -0.064).
    """
    history = [
        _analysis(sentiment=0.0, market=-0.0016, confidence=0.35, age_minutes=1),
        _analysis(sentiment=0.0, market=0.0, confidence=0.35, age_minutes=2),
    ]
    payload, bias = _bias(history, tmp_path)

    assert payload["item_count"] == 2
    assert payload["activity_factor"] == pytest.approx(0.4)
    assert payload["semantic_score"] == pytest.approx(payload["market_bias"] * 0.4, abs=1e-3)
    # The payload's own direction is what the panel renders.
    assert payload["direction"] == bias.direction
    assert bias.semantic_score == pytest.approx(payload["semantic_score"], abs=1e-3)


def test_thin_window_is_damped_below_direction_threshold(tmp_path):
    """Two articles agreeing strongly still must not call a direction on their own."""
    history = [
        _analysis(sentiment=-0.9, market=-0.0008, confidence=0.6, age_minutes=1),
        _analysis(sentiment=-0.9, market=-0.0008, confidence=0.6, age_minutes=2),
    ]
    payload, bias = _bias(history, tmp_path)
    assert abs(payload["market_bias"]) > 0.1, "undamped bias is directional"
    assert payload["direction"] == "neutral", "damped bias must not be"
    assert bias.direction == "neutral"


def test_full_window_is_not_damped(tmp_path):
    history = [
        _analysis(sentiment=-0.9, market=-0.0008, confidence=0.6, age_minutes=i)
        for i in range(1, 6)
    ]
    payload, bias = _bias(history, tmp_path)
    assert payload["activity_factor"] == pytest.approx(1.0)
    assert payload["semantic_score"] == pytest.approx(payload["market_bias"], abs=1e-3)
    assert payload["direction"] == "risk_off"
    assert bias.direction == "risk_off"


def test_bias_works_with_upstream_disabled(tmp_path):
    """The window bias is computed locally, so it survives a dead news service."""
    history = [_analysis(sentiment=-0.8, market=0.0009, confidence=0.6, age_minutes=1)]
    payload, bias = _bias(history, tmp_path)
    assert payload["source"] == "local_analysis_history"
    assert bias.status == "live", "local history must not read as a degraded fallback"
