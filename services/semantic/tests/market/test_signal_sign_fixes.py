"""Engine update C38 (2026-11-04): signal sizes and confidences that came out with the wrong sign
or an impossible value."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from market.artifact_reaction_scorer import CHANCE_CONFIDENCE, ArtifactReactionScorer
from market.historical_evidence_retriever import _metadata_filter_tiers

from test_usd_orientation import _context, _payload      # same directory (pytest prepend import mode)


def _signals(tmp_path: Path, payload: dict, pairs: list[str]) -> dict:
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    scorer = ArtifactReactionScorer.from_path(path)
    return {s.symbol: s for s in asyncio.run(scorer.score(_context(pairs)))}


def test_a_negative_magnitude_is_floored_at_zero(tmp_path: Path) -> None:
    signal = _signals(tmp_path, {**_payload("usd"), "magnitude_intercept": -0.0007}, ["USD/INR"])["USD/INR"]
    assert signal.direction == 1.0 and signal.magnitude == 0.0, "direction x magnitude can no longer flip sign"


def test_a_pair_without_a_usd_leg_gets_chance_confidence(tmp_path: Path) -> None:
    signals = _signals(tmp_path, _payload("usd"), ["EUR/JPY", "USD/JPY"])
    assert signals["EUR/JPY"].direction == 0.0 and signals["EUR/JPY"].confidence == CHANCE_CONFIDENCE
    assert signals["USD/JPY"].confidence > 0.9


def test_no_model_output_gets_chance_confidence(tmp_path: Path) -> None:
    payload = {**_payload("usd"), "direction_intercepts": [], "direction_coefficients": []}
    signal = _signals(tmp_path, payload, ["USD/JPY"])["USD/JPY"]
    assert signal.direction == 0.0 and signal.confidence == CHANCE_CONFIDENCE


def test_live_aggregator_counts_a_headline_once_per_pair() -> None:
    from market.live_sentiment import LiveSentimentAggregator

    async def run():
        agg = LiveSentimentAggregator()
        await agg.record("Fed holds rates", 0.4, 0.8, pair="EUR/USD")
        await agg.record("FED holds rates!", 0.4, 0.8, pair="EUR/USD")         # SSE copy / resend
        await agg.record("Fed holds rates", 0.4, 0.8, pair="USD/JPY")
        return await agg.aggregate("EUR/USD"), await agg.aggregate("USD/JPY")

    eur, jpy = asyncio.run(run())
    assert eur.item_count == 1 and jpy.item_count == 1


def test_evidence_for_a_pair_never_falls_back_to_other_pairs() -> None:
    tiers = [reason for _, reason in _metadata_filter_tiers(_context(["EUR/USD"]), 60)]
    assert tiers[-1] == "pair" and "semantic-similarity-only" not in tiers and "horizon" not in tiers
    unpaired = [reason for _, reason in _metadata_filter_tiers(_context([]), 60)]
    assert unpaired == ["horizon", "semantic-similarity-only"]
