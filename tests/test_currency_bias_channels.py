"""Risk and direction channels (ARBEX_CURRENCY_BIAS): separate, capped, off by default."""
from __future__ import annotations

import copy
from urllib.error import HTTPError

import pytest

from tests.test_semantic_engine import FixedNewsBiasProvider, make_opportunity  # installs the redis stub

from backend.core import currency_bias_channels as ch
from backend.core.semantic_engine import NewsBias, SemanticEngine, _clamp


def _bias() -> NewsBias:
    return NewsBias(pair="USD/INR", status="live", window_minutes=30, overall_sentiment=-0.7,
                    semantic_score=-0.42, confidence=0.8, confidence_adjustment=-0.04, direction="risk_off",
                    regime="high_conviction", item_count=4, bullish_count=0, bearish_count=4, neutral_count=0,
                    freshness_seconds=0.0, reason="NEWS RISK: test, confidence -0.04.")


class StubClient:
    def __init__(self, payload):
        self.payload = payload
        self.status = "live"
        self.calls = 0

    async def pair(self, pair):
        self.calls += 1
        return self.payload

    def cached(self, pair):
        return self.payload

    def age_seconds(self):
        return 1.0


def _pair(bias=None, label="neutral", confidence=0.0, expected=0.0, proximity=None):
    return {"relative_bias": bias, "label": label, "confidence": confidence,
            "components": {"base": "USD", "quote": "INR"},
            "event_risk": {"expected_move_bps": expected, "release_proximity": proximity}}


async def _score(engine, opp):
    published = []

    async def publisher(channel, message):
        published.append(message)

    engine._publisher = publisher
    await engine.process_raw_opp({"event_id": "evt", "opportunity": opp.to_dict()})
    message = published[0]
    message.pop("timestamp")
    # Wall-clock fields set at scoring time differ by a millisecond between two engines;
    # they are not part of the score, so parity compares everything else.
    for key in [k for k in (message.get("persistence") or {}) if k.endswith("_ts")]:
        message["persistence"].pop(key)
    return message


@pytest.mark.asyncio
async def test_flag_off_is_identical_to_todays_scoring(monkeypatch):
    monkeypatch.delenv("ARBEX_CURRENCY_BIAS", raising=False)
    default = SemanticEngine(news_bias_provider=FixedNewsBiasProvider(_bias()))
    assert default._currency_bias is False and default._currency_bias_client is None, "no client, no network"
    explicit_off = SemanticEngine(news_bias_provider=FixedNewsBiasProvider(_bias()), currency_bias=False,
                                  currency_bias_client=StubClient(_pair(-3.0, "bearish", 0.9, 50.0, "in 2 min")))
    opp = make_opportunity(confidence=0.82)
    a, b = await _score(default, opp), await _score(explicit_off, opp)
    assert a == b, "a configured client must not matter while the flag is off"
    # Today's formulas, written out: confidence + window adjustment; feasibility + adj x 100.
    assert a["opportunity"]["confidence_score"] == round(_clamp(0.82 - 0.04, 0.0, 1.0), 3)
    assert "semantic_channels" not in a and "Risk channel" not in a["ranking_reason"]
    assert explicit_off._currency_bias_client.calls == 0


@pytest.mark.asyncio
async def test_flag_on_applies_two_separate_capped_channels():
    payload = _pair(bias=-4.0, label="bearish", confidence=0.9, expected=40.0)   # opposes a long residual
    engine = SemanticEngine(news_bias_provider=FixedNewsBiasProvider(_bias()), currency_bias=True,
                            currency_bias_client=StubClient(payload))
    message = await _score(engine, make_opportunity(confidence=0.82))
    risk = message["semantic_channels"]["risk"]["penalty"]
    direction = message["semantic_channels"]["direction"]["penalty"]
    assert risk == pytest.approx(ch.RISK_CAP) and direction == pytest.approx(ch.DIRECTION_CAP)
    assert message["opportunity"]["confidence_score"] == pytest.approx(round(0.82 - 0.04 - risk - direction, 3))
    assert "Risk channel" in message["ranking_reason"] and "Direction channel" in message["ranking_reason"]


def test_channels_are_independent():
    calm_but_directional = _pair(bias=-2.0, label="bearish", confidence=0.8, expected=0.0)
    risky_but_neutral = _pair(bias=0.1, label="neutral", confidence=0.8, expected=12.0)
    assert ch.risk_channel(calm_but_directional).penalty == 0.0
    assert ch.direction_channel(calm_but_directional, +1).penalty == pytest.approx(0.01 * 2.0 * 0.8)
    assert ch.risk_channel(risky_but_neutral).penalty == pytest.approx(0.004 * (12.0 - 2.0))
    assert ch.direction_channel(risky_but_neutral, +1).penalty == 0.0


def test_direction_is_zero_when_neutral_aligned_low_confidence_or_no_legging():
    assert ch.direction_channel(_pair(bias=3.0, label="bullish", confidence=0.9), +1).penalty == 0.0, "aligned"
    assert ch.direction_channel(_pair(bias=-3.0, label="bearish", confidence=0.1), +1).penalty == 0.0, "low confidence"
    assert ch.direction_channel(_pair(bias=None), +1).penalty == 0.0
    assert ch.direction_channel(_pair(bias=-3.0, label="bearish", confidence=0.9), 0).penalty == 0.0
    assert ch.residual_exposure("cross_source") == 1 and ch.residual_exposure("triangular") == 0


def test_caps_hold_and_release_term():
    extreme = _pair(bias=-100.0, label="bearish", confidence=1.0, expected=1e6, proximity="in 1 min")
    assert ch.risk_channel(extreme).penalty == ch.RISK_CAP
    assert ch.direction_channel(extreme, +1).penalty == ch.DIRECTION_CAP
    assert ch.risk_channel(_pair(expected=0.0, proximity="just out")).penalty == pytest.approx(ch.RELEASE_PENALTY)


def test_calendar_proximity_dict_adds_the_release_penalty_and_reads_well():
    """The service sets release_proximity only inside its window, as a dict from the live calendar."""
    window = {"phase": "pre_release", "minutes": 4.0, "currency": "USD", "event": "Advance GDP q/q"}
    result = ch.risk_channel(_pair(expected=0.0, proximity=window))
    assert result.penalty == pytest.approx(ch.RELEASE_PENALTY)
    assert "in 4.0 min: USD Advance GDP q/q" in result.reason
    after = ch.risk_channel(_pair(expected=0.0, proximity={**window, "phase": "post_release", "minutes": 2.0}))
    assert "2.0 min ago" in after.reason
    assert ch.risk_channel(_pair(expected=0.0, proximity=None)).penalty == 0.0


@pytest.mark.asyncio
async def test_flag_off_unchanged_with_a_calendar_payload(monkeypatch):
    monkeypatch.delenv("ARBEX_CURRENCY_BIAS", raising=False)
    window = {"phase": "post_release", "minutes": 1.0, "currency": "USD", "event": "CPI m/m"}
    default = SemanticEngine(news_bias_provider=FixedNewsBiasProvider(_bias()))
    off = SemanticEngine(news_bias_provider=FixedNewsBiasProvider(_bias()), currency_bias=False,
                         currency_bias_client=StubClient(_pair(-3.0, "bearish", 0.9, 50.0, window)))
    opp = make_opportunity(confidence=0.82)
    assert await _score(default, opp) == await _score(off, opp)
    assert off._currency_bias_client.calls == 0


def test_snapshot_uses_cached_values_only():
    class NoNetworkClient(StubClient):
        async def pair(self, pair):
            raise AssertionError("snapshot must not fetch")

    client = NoNetworkClient(_pair(bias=-2.0, label="bearish", confidence=0.8, expected=10.0))
    snap = ch.channel_snapshot("EUR/USD", "cross_source", window_bias={"semantic_score": 0.1}, client=client, applied=False)
    assert snap["risk_channel"]["penalty"] > 0 and snap["direction_channel"]["penalty"] > 0
    assert snap["applied"] is False and snap["window_bias"] == {"semantic_score": 0.1}
    empty = ch.channel_snapshot("EUR/USD", "cross_source", window_bias=None, client=StubClient(None), applied=False)
    assert empty["risk_channel"] is None and empty["direction_channel"] is None


def test_bridge_treats_a_501_as_capability_off(monkeypatch):
    from backend.core.sentiment_bridge import SentimentBridge

    bridge = SentimentBridge()
    monkeypatch.setattr(bridge, "_enabled", True)
    monkeypatch.setattr(bridge, "_base_url", "http://127.0.0.1:1")

    def fake_fetch(path, params, *, record=True):
        assert path == "/currency-index" and record is False
        raise HTTPError(path, 501, "off", None, None)

    monkeypatch.setattr(bridge, "_fetch_json", fake_fetch)
    assert bridge.fetch_currency_index() is None
    monkeypatch.setattr(bridge, "_fetch_json", lambda *a, **k: {"enabled": True, "pairs": {}})
    assert bridge.fetch_currency_index() == {"enabled": True, "pairs": {}}
