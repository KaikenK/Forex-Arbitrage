"""Display-only additions for the redesigned dashboard: /system/health and the shadow block on scored
messages. Neither writes to the recorder, the snapshot or the prediction log."""
from __future__ import annotations

import pytest

from tests.test_inprocess_scorer import _factory, make_opportunity  # installs the redis stub

from fastapi.testclient import TestClient

from backend.server.inprocess_scorer import InProcessScorer
from backend.server.main import app
from backend.server.system_health import GapTracker, SHADOW_BLOCK_FIELDS, ShadowCache, system_health


def test_gap_tracker_records_silences_over_the_threshold_only():
    g = GapTracker(threshold_ms=5_000)
    g.observe([1_000, 1_500])
    g.observe([2_000, None])
    g.observe([9_000])                    # 7 s after the newest receipt: a gap
    g.observe([8_000])                    # an older quote never moves the clock back
    g.observe([12_000])                   # 3 s: not a gap
    assert g.gap_count == 1 and list(g.gaps) == [{"start_ms": 2_000, "end_ms": 9_000, "seconds": 7.0}]
    assert g.last_recv_ms == 12_000
    g.observe([None])
    assert g.last_recv_ms == 12_000


def test_shadow_cache_keeps_the_newest_rows_and_only_display_fields():
    c = ShadowCache(keep=2)
    for i in range(3):
        c.put({"id": f"o{i}", "timestamp_ms": 100 + i, "adjusted_confidence_score": 0.4, "written_at": 1.0})
    assert c.get("o0") is None and c.get("o2")["adjusted_confidence_score"] == 0.4
    assert set(c.get("o2")) == set(SHADOW_BLOCK_FIELDS) and c.last_row_ms == 102
    block = c.get("o2")
    block["adjusted_confidence_score"] = 0.0                     # callers get a copy
    assert c.get("o2")["adjusted_confidence_score"] == 0.4


@pytest.mark.asyncio
async def test_scored_message_carries_the_shadow_block_only_when_there_is_one():
    sent = []

    async def broadcast(msg):
        sent.append(msg)

    opp = make_opportunity()
    cache = ShadowCache()
    cache.put({"id": opp.id, "timestamp_ms": opp.timestamp_ms, "confidence_score": 0.55,
               "adjusted_confidence_score": 0.47, "stale_caution": 0.0, "risk_penalty": 0.08})
    with_shadow = InProcessScorer(_factory, broadcast, mode="on", shadow_lookup=cache.get)
    await with_shadow(opp)
    without = InProcessScorer(_factory, broadcast, mode="on")
    await without(opp)
    assert sent[0]["shadow"]["adjusted_confidence_score"] == 0.47
    assert "shadow" not in sent[1]


def test_health_payload_shape():
    class Rec:
        rows_written, session_dir = 42, "C:/rec/2026-11-05"

    class Writer:
        rows_written = 7

    g = GapTracker()
    g.observe([1_000])
    out = system_health(started_at=100.0, mode="LIVE_USDINR_EXCHANGES", gaps=g, recorder=Rec(), shadow_enabled=True,
                        shadow_writer=Writer(), shadow_cache=ShadowCache(), shadow_root="C:/shadow",
                        legs=[{"id": "a", "healthy": True}, {"id": "b", "healthy": False}],
                        semantic={"available": True, "last_error": None, "base_url": "x"}, now=160.0)
    assert out["backend"]["uptime_s"] == 60.0 and out["recorder"]["rows_written"] == 42
    assert out["shadow"]["rows_written"] == 7 and out["legs"] == {"total": 2, "healthy": 1, "stale": ["b"]}
    assert set(out["semantic_service"]) == {"available", "last_success_at", "last_error", "last_latency_ms"}


def test_route_answers_without_a_live_feed():
    body = TestClient(app).get("/system/health").json()
    assert {"backend", "recorder", "shadow", "legs", "semantic_service"} <= set(body)
