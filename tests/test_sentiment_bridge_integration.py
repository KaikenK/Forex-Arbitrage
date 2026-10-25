"""End-to-end integration test for SentimentBridge over a real HTTP/SSE stub.

Unlike test_sentiment_bridge.py (which unit-tests logic in-process), this stands up
a real threaded HTTP server and drives the bridge through its actual urlopen / SSE
parse / ingest-thread / analysis-worker path, mirroring the semantic service contract
(versioned routes under /v1, as services/semantic serves them).
"""
from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.core.sentiment_bridge import SentimentBridge

API_PREFIX = "/v1"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    # A 404 from the stub must never turn into a live RSS fetch (news_fallback.py).
    monkeypatch.setenv("NEWS_RSS_FALLBACK", "0")
    monkeypatch.delenv("SEMANTIC_API_PREFIX", raising=False)

# Feed cycle mixes allowlisted pairs with the junk tokens the upstream regex emits.
_EVENTS = [
    {"headline": "RBI holds repo rate, rupee steady", "pair": "USD/INR", "category": "central_bank", "source": "FINNHUB"},
    {"headline": "Fed higher for longer, dollar firms", "pair": "EUR/USD", "category": "fed_decision", "source": "RSS"},
    {"headline": "BSE 400 SMID mid-cap index rebounds", "pair": "MID/CAP", "category": "general", "source": "RSS"},
    {"headline": "Traders in wait-and-see mode", "pair": "AND/SEE", "category": "macro_data", "source": "RSS"},
    {"headline": "Yen slips as BOJ holds", "pair": "USD/JPY", "category": "central_bank", "source": "FINNHUB"},
]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, body: bytes, status: int = 200):
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self) -> str:
        return self.path[len(API_PREFIX):] if self.path.startswith(API_PREFIX + "/") else "<unversioned>"

    def do_GET(self):
        path = self._route()
        if path == "/feed":
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            i = 0
            try:
                while True:
                    ev = dict(
                        _EVENTS[i % len(_EVENTS)],
                        event_id=f"evt-{i}",
                        url=None,
                        timestamp=datetime.now(UTC).isoformat(),
                        provider="rss",
                        provenance="upstream",
                        feed_mode="live",
                    )
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                    self.wfile.flush()
                    i += 1
                    time.sleep(0.05)
            except OSError:
                return
        elif path.startswith("/sentiment/live"):
            self.server.live_hits += 1  # type: ignore[attr-defined]
            self._json(json.dumps({
                "pair": "USD/INR", "overall_sentiment": 0.2, "confidence": 0.5,
                "bullish_count": 2, "bearish_count": 1, "neutral_count": 1,
                "item_count": 4, "top_drivers": [], "window_minutes": 30,
                "updated_at": datetime.now(UTC).isoformat(),
            }).encode())
        else:
            self._json(b'{"error":"not found"}', status=404)

    def do_POST(self):
        if self._route() == "/analyze":
            length = int(self.headers.get("content-length", 0))
            self.rfile.read(length)
            self._json(json.dumps({"result": {"event_id": "", "confidence": 0.6, "signal_rationale": "stub"}}).encode())
        else:
            self._json(b'{"error":"not found"}', status=404)


@pytest.fixture()
def stub_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.live_hits = 0  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()


def _wait_until(predicate, timeout=5.0, interval=0.05):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_ingest_pipeline_end_to_end(stub_server, tmp_path):
    port = stub_server.server_address[1]
    bridge = SentimentBridge(base_url=f"http://127.0.0.1:{port}")
    bridge._runtime_history_root = tmp_path

    bridge.start_live_ingest()
    try:
        # Wait for the ingest thread + analysis worker to process allowlisted events.
        assert _wait_until(lambda: (tmp_path / "usdinr").exists())
        assert _wait_until(lambda: "USD/INR" in bridge._analysis_history)
    finally:
        bridge.stop_live_ingest()

    dirs = {p.name for p in tmp_path.iterdir() if p.is_dir()}
    # Junk pairs from the feed must never be materialized.
    assert {"midcap", "andsee"}.isdisjoint(dirs)
    # "predictions" is the per-pair live prediction log, not a pair directory.
    assert dirs <= {"usdinr", "eurusd", "usdjpy", "predictions"}

    # Cache-only snapshot is served immediately from what ingest populated.
    t0 = time.perf_counter()
    snap = bridge.get_feed_snapshot("USD/INR", limit=5)
    assert time.perf_counter() - t0 < 1.0
    assert snap["items"]
    assert all(item["pair"] == "USD/INR" for item in snap["items"])


def test_live_sentiment_uses_ttl_cache(stub_server, tmp_path):
    port = stub_server.server_address[1]
    bridge = SentimentBridge(base_url=f"http://127.0.0.1:{port}")
    bridge._runtime_history_root = tmp_path

    first = bridge.get_live_sentiment("USD/INR", 30)
    hits_after_first = stub_server.live_hits  # type: ignore[attr-defined]
    second = bridge.get_live_sentiment("USD/INR", 30)

    assert first["item_count"] == 4
    assert second["item_count"] == 4
    # Second call within the TTL is served from cache -> no extra upstream hit.
    assert stub_server.live_hits == hits_after_first  # type: ignore[attr-defined]
