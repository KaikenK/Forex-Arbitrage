"""Regression tests for the news_stream <-> Forex-Arbitrage SentimentBridge.

Covers the two integration faults fixed in this branch:
  1. Junk-pair fan-out: the upstream feed regex emits ordinary hyphenated words
     as "pairs" (MID/CAP from "mid-cap", AND/SEE from "wait-and-see"). The bridge
     must drop anything outside the configured allowlist instead of spawning a
     runtime directory + JSONL file per junk token.
  2. Blocking request path: /sentiment/feed/live and /sentiment/history/live must
     serve from the background-ingest cache only and never open the SSE /feed
     connection synchronously inside the request thread.
"""
from __future__ import annotations

import pytest

import backend.core.sentiment_bridge as sb
from backend.core.sentiment_bridge import SentimentBridge


@pytest.fixture(autouse=True)
def _no_rss_fallback(monkeypatch):
    """These tests pin the news_stream path. The direct-RSS fallback (news_fallback.py)
    is tested on its own with a fake fetcher; left on here it would reach the internet."""
    monkeypatch.setenv("NEWS_RSS_FALLBACK", "0")


def _make_bridge(tmp_path, **kwargs) -> SentimentBridge:
    bridge = SentimentBridge(base_url="http://127.0.0.1:9", **kwargs)
    bridge._runtime_history_root = tmp_path
    return bridge


def _feed_event(pair: str, headline: str = "headline") -> dict:
    return {"pair": pair, "headline": headline, "source": "RSS", "category": "general"}


# --------------------------------------------------------------------------
# Fault 1: pair allowlist
# --------------------------------------------------------------------------

def test_allowlist_drops_junk_pairs(tmp_path):
    bridge = _make_bridge(tmp_path)
    for junk in ("MID/CAP", "AND/SEE", "HAS/NOT", "TWO/AND"):
        assert bridge._tracked_pairs_for_feed_event(_feed_event(junk)) == []


def test_allowlist_drops_valid_but_unlisted_pair(tmp_path):
    # NZD/CAD is a real FX pair but outside the default allowlist -> dropped.
    bridge = _make_bridge(tmp_path)
    assert bridge._tracked_pairs_for_feed_event(_feed_event("NZD/CAD")) == []


def test_allowlist_keeps_majors(tmp_path):
    bridge = _make_bridge(tmp_path)
    for good in ("USD/INR", "EUR/USD", "USD/JPY", "AUD/USD"):
        assert bridge._tracked_pairs_for_feed_event(_feed_event(good)) == [good]


def test_macro_events_fan_out_to_all_study_pairs_usdinr_first(tmp_path):
    # Changed 2026-10-24 (multi-pair phase): macro headlines used to collapse to
    # USD/INR only; they are a dollar shock to every study pair.
    bridge = _make_bridge(tmp_path)
    for macro in ("", "MACRO", "GLOBAL", "RISK"):
        assert bridge._tracked_pairs_for_feed_event(_feed_event(macro)) == list(sb.STUDY_PAIRS)
    assert sb.STUDY_PAIRS[0] == "USD/INR"


def test_allowlist_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_STREAM_PAIR_ALLOWLIST", "USD/INR, EUR/USD")
    bridge = _make_bridge(tmp_path)
    assert bridge._tracked_pairs_for_feed_event(_feed_event("EUR/USD")) == ["EUR/USD"]
    # USD/JPY is a default major but excluded by the override.
    assert bridge._tracked_pairs_for_feed_event(_feed_event("USD/JPY")) == []


def test_handle_feed_event_skips_junk_no_persistence(tmp_path, monkeypatch):
    bridge = _make_bridge(tmp_path)
    monkeypatch.setattr(bridge, "_analyze_feed_event", lambda event: None)

    bridge._handle_feed_event(_feed_event("MID/CAP", "mid-cap stocks rally"))
    assert bridge._feed_items == {}
    assert not (tmp_path / "midcap").exists()

    bridge._handle_feed_event(_feed_event("USD/INR", "rupee steady"))
    assert "USD/INR" in bridge._feed_items
    assert (tmp_path / "usdinr" / "live_feed.jsonl").exists()


# --------------------------------------------------------------------------
# Fault 2: request path must not touch the network
# --------------------------------------------------------------------------

def test_feed_and_history_snapshot_never_open_network(tmp_path, monkeypatch):
    bridge = _make_bridge(tmp_path)

    def _boom(*args, **kwargs):  # any HTTP call in the request path is a bug
        raise AssertionError("request path opened a network connection")

    monkeypatch.setattr(sb, "urlopen", _boom)

    feed = bridge.get_feed_snapshot("USD/INR", limit=5)
    history = bridge.get_analysis_history("USD/INR", limit=5)

    assert feed["pair"] == "USD/INR"
    assert history["pair"] == "USD/INR"
    assert feed["items"] == []
    assert history["items"] == []


# --------------------------------------------------------------------------
# Fault 5: /analyze is deferred off the SSE read path
# --------------------------------------------------------------------------

def test_analysis_is_deferred_not_called_inline(tmp_path, monkeypatch):
    bridge = _make_bridge(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(bridge, "_analyze_feed_event", lambda event: calls.append(event["event_id"]))

    bridge._handle_feed_event(_feed_event("USD/INR", "rupee steady"))

    # Feed persisted inline, but analysis only enqueued (worker not started here).
    assert "USD/INR" in bridge._feed_items
    assert calls == []
    assert bridge._analysis_queue.qsize() == 1


def test_analysis_worker_drains_queue(tmp_path, monkeypatch):
    bridge = _make_bridge(tmp_path)
    processed: list[str] = []
    monkeypatch.setattr(
        bridge, "_analyze_feed_event",
        lambda event: {"event_id": event["event_id"], "confidence": 0.5},
    )
    monkeypatch.setattr(bridge, "_persist_runtime_record", lambda path, payload: None)

    import threading
    bridge._ingest_stop = threading.Event()
    worker = threading.Thread(target=bridge._run_analysis_worker, daemon=True)
    worker.start()
    try:
        bridge._handle_feed_event(_feed_event("USD/INR", "rupee steady"))
        bridge._analysis_queue.join()
    finally:
        bridge._ingest_stop.set()
        worker.join(timeout=2.0)

    assert "USD/INR" in bridge._analysis_history


# --------------------------------------------------------------------------
# Fault 4: status state is written through locked recorders
# --------------------------------------------------------------------------

def test_status_recorders_update_availability(tmp_path):
    bridge = _make_bridge(tmp_path)

    assert bridge.get_status("USD/INR")["news_stream"]["available"] is False

    bridge._record_success(12.5)
    status = bridge.get_status("USD/INR")["news_stream"]
    assert status["available"] is True
    assert status["last_error"] is None
    assert status["last_latency_ms"] == 12.5

    bridge._record_error("boom")
    status = bridge.get_status("USD/INR")["news_stream"]
    assert status["available"] is False
    assert status["last_error"] == "boom"


def test_sse_activity_clears_a_stale_error(tmp_path):
    """A delivered SSE event proves liveness and must clear a latched error.

    availability is `last_success_at is not None and last_error is None`, and the
    ingest loop never recorded success -- so one failed HTTP call pinned the status
    chip to "service degraded" forever while live headlines kept arriving.
    """
    bridge = _make_bridge(tmp_path)

    bridge._record_error("connection refused")
    assert bridge.get_status("USD/INR")["news_stream"]["available"] is False

    bridge._record_stream_activity()

    status = bridge.get_status("USD/INR")["news_stream"]
    assert status["available"] is True
    assert status["last_error"] is None


def test_stream_activity_does_not_fake_a_latency_number(tmp_path):
    """Streamed events have no round-trip latency, so none should be reported."""
    bridge = _make_bridge(tmp_path)
    bridge._record_stream_activity()
    assert bridge.get_status("USD/INR")["news_stream"]["last_latency_ms"] is None
