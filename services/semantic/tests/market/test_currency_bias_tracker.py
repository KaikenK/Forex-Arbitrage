"""Live per-currency bias (/v1/currency-index): same formula as research H6, live state only."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from market import currency_bias_core as core
from market.currency_bias_tracker import CurrencyBiasTracker, rolling_novelty
from market.runtime_factory import SentimentTap
from retrieval.embedder import LocalHashingEmbedder

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def _research_root() -> Path | None:
    env = os.environ.get("ARBEX_RESEARCH_ROOT")
    candidates = [Path(env)] if env else []
    here = Path(__file__).resolve()
    candidates += [here.parents[4] / "research", here.parents[5] / "Forex-Arbitrage" / "research"]
    for path in candidates:
        if (path / "semantic" / "currency_bias_core.py").exists():
            return path
    return None


def _tracker(clock, **settings) -> CurrencyBiasTracker:
    embedder = LocalHashingEmbedder(dimensions=64)
    return CurrencyBiasTracker(embedder.embed, clock=clock, settings={"zscore_min_history": 3, **settings})


def test_formula_is_byte_identical_to_the_research_copy() -> None:
    root = _research_root()
    if root is None:
        pytest.skip("research checkout not found (set ARBEX_RESEARCH_ROOT)")
    research = (root / "semantic" / "currency_bias_core.py").read_bytes()
    service = Path(core.__file__).read_bytes()
    assert hashlib.sha256(research).hexdigest() == hashlib.sha256(service).hexdigest()


def test_live_novelty_matches_the_research_definition() -> None:
    root = _research_root()
    if root is None:
        pytest.skip("research checkout not found")
    sys.path.insert(0, str(root.parent))
    try:
        import numpy as np
        from research.semantic.novelty import compute_novelty
    finally:
        sys.path.pop(0)
    rng = np.random.default_rng(0)
    vectors = rng.normal(size=(8, 16))
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    times = [NOW + timedelta(hours=i) for i in range(8)]
    expected = {s.event_id: s.novelty for s in compute_novelty([str(i) for i in range(8)], times, vectors)}
    for i in range(8):
        live = rolling_novelty([list(v) for v in vectors[:i]], list(vectors[i]))
        assert (live is None and expected[str(i)] is None) or live == pytest.approx(expected[str(i)])


def test_usd_bullish_eur_bearish_makes_eur_usd_bearish() -> None:
    clock = {"now": NOW}
    tracker = _tracker(lambda: clock["now"])
    stamp = NOW - timedelta(hours=6)
    for k in range(6):   # warm-up history with mixed tone
        tracker.observe(headline=f"Dollar mixed as traders await data {k}", published_at=stamp - timedelta(hours=30 + k),
                        polarity=0.1 * (-1) ** k, confidence=0.9)
        tracker.observe(headline=f"Euro steady in quiet markets {k}", published_at=stamp - timedelta(hours=30 + k),
                        polarity=-0.1 * (-1) ** k, confidence=0.9)
    for k in range(4):
        tracker.observe(headline=f"Dollar surges as payrolls beat expectations, market cheers {k}",
                        published_at=stamp + timedelta(minutes=k), polarity=0.9, confidence=0.95)
        tracker.observe(headline=f"Euro slumps as eurozone economy weakens further {k}",
                        published_at=stamp + timedelta(minutes=k), polarity=-0.9, confidence=0.95)
    snap = tracker.snapshot()
    assert snap["currencies"]["USD"]["label"] == "bullish"
    assert snap["currencies"]["EUR"]["label"] == "bearish"
    pair = snap["pairs"]["EUR/USD"]
    assert pair["relative_bias"] < 0 and pair["label"] == "bearish"
    assert pair["components"]["base"] == "EUR" and pair["components"]["quote"] == "USD"
    assert snap["pairs"]["USD/JPY"]["relative_bias"] > 0
    assert snap["currencies"]["USD"]["top_headlines"], "contributing headlines are reported"


def test_headline_younger_than_15_minutes_is_not_used_yet() -> None:
    clock = {"now": NOW}
    tracker = _tracker(lambda: clock["now"])
    tracker.observe(headline="Yen jumps as BoJ surprises markets", published_at=NOW - timedelta(minutes=5),
                    polarity=0.8, confidence=0.9)
    assert tracker.snapshot()["currencies"]["JPY"]["headline_count"] == 0
    clock["now"] = NOW + timedelta(minutes=11)
    assert tracker.snapshot()["currencies"]["JPY"]["headline_count"] == 1


def test_payload_declares_provisional_settings_gaps_and_horizon(tmp_path: Path) -> None:
    tracker = _tracker(lambda: NOW)
    snap = tracker.snapshot({"pair": "EUR/USD"})
    assert snap["horizon"]["label"] == "none established"
    assert snap["provisional_settings"]["novelty_missing"] == "as_novel"
    assert any("consensus" in gap for gap in snap["live_input_gaps"])
    assert snap["pairs"]["EUR/USD"]["event_risk"]["release_proximity"] is None
    horizon = tmp_path / "h6_horizon.json"
    horizon.write_text(json.dumps({"horizon_minutes": 240, "label": "240 min"}), encoding="utf-8")
    tracker = CurrencyBiasTracker(LocalHashingEmbedder(16).embed, horizon_file=horizon, clock=lambda: NOW)
    assert tracker.snapshot()["horizon"]["horizon_minutes"] == 240


def test_event_risk_uses_recent_reaction_magnitudes() -> None:
    tracker = _tracker(lambda: NOW)
    tracker.observe(headline="Dollar jumps after CPI surprise, markets react", published_at=NOW - timedelta(minutes=20),
                    polarity=0.5, confidence=0.8,
                    pair_scores={"EUR/USD": {"magnitude": 0.0012, "confidence": 0.7}})
    risk = tracker.snapshot()["pairs"]["EUR/USD"]["event_risk"]
    assert risk["expected_move_bps"] == pytest.approx(12.0 * core.decay(20 / 60), abs=1e-3)
    assert risk["recent_scored_items"] == 1


@pytest.mark.asyncio
async def test_sentiment_tap_passes_the_score_through_unchanged() -> None:
    seen = {}

    class Inner:
        async def score(self, context):
            return "inference"

    class Ctx:
        class event:
            event_id = "e1"

    tap = SentimentTap(Inner(), lambda event_id, inference: seen.update({event_id: inference}))
    assert await tap.score(Ctx()) == "inference"
    assert seen == {"e1": "inference"}


@pytest.mark.asyncio
async def test_route_serves_the_tracker_when_wired() -> None:
    from api.main import build_app
    from tests.api.test_routes import _build_service, _request

    tracker = _tracker(lambda: NOW)
    status, payload = await _request(build_app(_build_service(), currency_index_provider=tracker.snapshot),
                                     "GET", "/v1/currency-index")
    assert status == 200 and payload["enabled"] is True
    assert set(payload["pairs"]) == {"EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/INR"}
