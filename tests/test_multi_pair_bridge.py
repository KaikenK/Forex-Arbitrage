"""Live multi-pair routing through the semantic microservice.

One headline -> ONE /analyze call naming every pair -> each pair filed with its own
reaction-model score (never a cross-pair average) -> one prediction-log row per pair.
No network: the service call is faked.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

import backend.core.sentiment_bridge as sb
from backend.core.sentiment_bridge import SentimentBridge

NOW = datetime(2026, 10, 24, 13, 31, tzinfo=UTC)

# EUR/USD down and USD/JPY up: the same dollar-strength move. Averaging would cancel it.
PAIR_SCORES = {
    "USD/INR": {"market_score": 0.0010, "confidence": 0.60, "direction": 1.0, "magnitude": 0.0010, "horizon_minutes": 60.0},
    "EUR/USD": {"market_score": -0.0020, "confidence": 0.55, "direction": -1.0, "magnitude": 0.0020, "horizon_minutes": 60.0},
    "GBP/USD": {"market_score": -0.0015, "confidence": 0.50, "direction": -1.0, "magnitude": 0.0015, "horizon_minutes": 60.0},
    "USD/JPY": {"market_score": 0.0025, "confidence": 0.65, "direction": 1.0, "magnitude": 0.0025, "horizon_minutes": 60.0},
    # USD/CHF deliberately missing: the service gave no score -> must not be filed.
}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("NEWS_RSS_FALLBACK", "0")
    monkeypatch.delenv("SEMANTIC_API_PREFIX", raising=False)
    monkeypatch.delenv("SEMANTIC_SERVICE_URL", raising=False)
    monkeypatch.delenv("NEWS_STREAM_BASE_URL", raising=False)
    monkeypatch.delenv("NEWS_STREAM_PAIR_ALLOWLIST", raising=False)


def _bridge(tmp_path) -> SentimentBridge:
    bridge = SentimentBridge(base_url="http://127.0.0.1:9", clock=lambda: NOW)
    bridge._runtime_history_root = tmp_path
    return bridge


def _fake_service(calls: list[tuple[str, dict]]):
    def post(path, payload):
        calls.append((path, payload))
        return {"result": {
            "event_id": "svc-1", "sentiment_score": 0.4, "market_score": 0.0010, "confidence": 0.60,
            "trading_signal": "BUY", "produced_at": NOW.isoformat(), "pair_scores": PAIR_SCORES,
            "affected_symbols": list(PAIR_SCORES),
        }}
    return post


def _macro_event() -> dict:
    return {"pair": "MACRO", "headline": "US payrolls beat expectations", "source": "RSS",
            "category": "employment", "event_id": "evt-1", "timestamp": NOW.isoformat()}


def _run(bridge, monkeypatch) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(bridge, "_post_json", _fake_service(calls))
    bridge._handle_feed_event(_macro_event())
    assert bridge._analysis_queue.qsize() == 1, "one job per headline, not one per pair"
    bridge._process_analysis_job(bridge._analysis_queue.get_nowait())
    return calls


def test_macro_headline_is_analysed_once_with_every_study_pair(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path)
    calls = _run(bridge, monkeypatch)

    assert len(calls) == 1
    path, payload = calls[0]
    assert path == "/analyze"
    assert payload["currency_pair"] == "USD/INR"
    assert payload["currency_pairs"] == ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF"]
    for pair in sb.STUDY_PAIRS:
        assert bridge._feed_items[pair], f"headline filed in the {pair} feed"


def test_each_pair_gets_its_own_score_never_an_average(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path)
    _run(bridge, monkeypatch)

    eur = bridge._analysis_history["EUR/USD"][0]
    jpy = bridge._analysis_history["USD/JPY"][0]
    inr = bridge._analysis_history["USD/INR"][0]
    assert eur["market_score"] == pytest.approx(-0.0020) and eur["confidence"] == pytest.approx(0.55)
    assert jpy["market_score"] == pytest.approx(0.0025)
    assert inr["market_score"] == pytest.approx(0.0010) and inr["signal_scope"] == "article"
    # Secondary pairs never inherit the primary pair's risk-gated trading signal.
    assert eur["trading_signal"] == "SELL" and jpy["trading_signal"] == "BUY"
    assert eur["signal_scope"] == "pair_score" and eur["primary_pair"] == "USD/INR"

    # Window bias per pair reflects that pair's own direction.
    assert bridge.get_window_bias("EUR/USD", 30)["market_bias"] < 0
    assert bridge.get_window_bias("USD/JPY", 30)["market_bias"] > 0


def test_pair_without_a_service_score_is_not_filed(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path)
    _run(bridge, monkeypatch)
    assert "USD/CHF" not in bridge._analysis_history
    assert bridge._feed_items["USD/CHF"], "the headline itself is still in the CHF feed"


def test_explanation_is_pair_specific(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path)
    _run(bridge, monkeypatch)
    eur = bridge.get_explanation("EUR/USD", "evt-1")
    inr = bridge.get_explanation("USD/INR", "evt-1")
    assert eur["analysis"]["market_score"] == pytest.approx(-0.0020)
    assert inr["analysis"]["market_score"] == pytest.approx(0.0010)


def test_prediction_log_has_one_timestamped_row_per_filed_pair(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path)
    _run(bridge, monkeypatch)
    rows = [json.loads(line) for line in
            (tmp_path / "predictions" / "live_predictions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["pair"] for row in rows] == ["USD/INR", "EUR/USD", "GBP/USD", "USD/JPY"]
    for row in rows:
        assert row["logged_at"] and row["published_at"] and row["event_id"] == "evt-1"
        assert row["headline"] == "US payrolls beat expectations"
        assert row["horizon_minutes"] == 60.0
    assert rows[1]["market_score"] == pytest.approx(-0.0020) and rows[1]["direction"] == -1.0


def test_single_pair_event_makes_a_plain_single_pair_call(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path)
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(bridge, "_post_json", _fake_service(calls))
    bridge._handle_feed_event({**_macro_event(), "pair": "EUR/USD"})
    bridge._process_analysis_job(bridge._analysis_queue.get_nowait())
    assert calls[0][1]["currency_pair"] == "EUR/USD" and "currency_pairs" not in calls[0][1]


def test_allowlist_still_limits_macro_fan_out(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_STREAM_PAIR_ALLOWLIST", "USD/INR,USD/JPY")
    bridge = _bridge(tmp_path)
    assert bridge._tracked_pairs_for_feed_event(_macro_event()) == ["USD/INR", "USD/JPY"]


def test_service_url_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_STREAM_BASE_URL", "http://legacy:9000")
    monkeypatch.setenv("SEMANTIC_SERVICE_URL", "http://semantic:9100/")
    bridge = SentimentBridge()
    assert bridge._service_url("/analyze") == "http://semantic:9100/v1/analyze", "versioned by default"

    monkeypatch.delenv("SEMANTIC_SERVICE_URL")
    monkeypatch.setenv("SEMANTIC_API_PREFIX", "")
    legacy = SentimentBridge()
    assert legacy._service_url("/feed") == "http://legacy:9000/feed", "legacy unversioned news_stream"
