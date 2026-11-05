"""Phase 2 engine switches (backend/core/semantic_apply.py): every switch defaults off and, off,
changes nothing that is recorded; on, each does what RESEARCH_TRACKER §6 says."""
from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime, timedelta

import pytest

from backend.core import semantic_apply as sa
from backend.core.semantic_engine import LiveNewsBiasClient
from backend.core.sentiment_bridge import SentimentBridge

FLAGS = (sa.APPLY_ENV, sa.DIRECTION_SOURCE_ENV, sa.FX_FILTER_ENV)


@pytest.fixture(autouse=True)
def _flags_off(monkeypatch):
    for name in FLAGS:
        monkeypatch.delenv(name, raising=False)


def _analysis(headline: str, *, market: float, age_minutes: int = 2, confidence: float = 0.8) -> dict:
    return {"event_id": f"evt-{headline}", "sentiment_score": 0.1, "market_score": market,
            "confidence": confidence, "trading_signal": "BUY" if market > 0 else "SELL",
            "produced_at": (datetime.now(UTC) - timedelta(minutes=age_minutes)).isoformat(),
            "feed": {"headline": headline, "source": "RSS"}}


def _bridge(pair: str, history: list[dict], tmp_path) -> SentimentBridge:
    bridge = SentimentBridge(enabled=False)          # offline: no upstream call
    bridge._runtime_history_root = tmp_path
    bridge._runtime_cache_loaded.add(pair)
    bridge._analysis_history[pair] = history
    return bridge


# ---------------------------------------------------------------- defaults

def test_every_switch_defaults_off_and_ignores_other_values(monkeypatch):
    assert (sa.apply_mode(), sa.direction_source(), sa.fx_relevance_filter_enabled()) == ("off", "v1", False)
    monkeypatch.setenv(sa.APPLY_ENV, "direction")
    monkeypatch.setenv(sa.DIRECTION_SOURCE_ENV, "v2")
    monkeypatch.setenv(sa.FX_FILTER_ENV, "0")
    assert (sa.apply_mode(), sa.direction_source(), sa.fx_relevance_filter_enabled()) == ("off", "v1", False)
    monkeypatch.setenv(sa.APPLY_ENV, "Risk")
    monkeypatch.setenv(sa.DIRECTION_SOURCE_ENV, "currency_index")
    monkeypatch.setenv(sa.FX_FILTER_ENV, "1")
    assert (sa.apply_mode(), sa.direction_source(), sa.fx_relevance_filter_enabled()) == ("risk", "currency_index", True)


# ---------------------------------------------------------------- ARBEX_APPLY_SEMANTIC=risk

def test_stale_caution_ramps_from_one_poll_to_the_detector_limit():
    assert sa.stale_caution(None) == 0.0 and sa.stale_caution(800) == 0.0 and sa.stale_caution(1_000) == 0.0
    assert sa.stale_caution(3_000) == pytest.approx(0.025)
    assert sa.stale_caution(5_000) == pytest.approx(0.05) and sa.stale_caution(60_000) == pytest.approx(0.05)


def test_older_leg_age_uses_detection_time_and_the_older_leg():
    row = {"timestamp_ms": 10_000, "buy_source": "spot_truefx_eurusd", "sell_source": "spot_kraken_eurusd"}
    assert sa.older_leg_age_ms(row, {"spot_truefx_eurusd": 9_800, "spot_kraken_eurusd": 7_000}) == 3_000
    assert sa.older_leg_age_ms(row, {"spot_truefx_eurusd": 10_200, "spot_kraken_eurusd": 9_900}) == 100
    assert sa.older_leg_age_ms(row, {"spot_truefx_eurusd": 9_800}) is None


def test_apply_lowers_confidence_by_risk_and_caution_and_keeps_the_raw_score():
    row = {"confidence_score": 0.5}
    snapshot = {"applied": False, "risk_channel": {"penalty": 0.06}, "direction_channel": {"penalty": 0.03}}
    applied = sa.apply_to_row(row, snapshot, older_leg_age_ms=3_000)
    assert row["raw_confidence_score"] == 0.5
    assert row["confidence_score"] == pytest.approx(0.5 - 0.06 - 0.025, abs=1e-3)    # direction NOT applied
    assert snapshot["applied"] is True and applied["mode"] == "risk" and not applied["risk_unavailable"]
    low = {"confidence_score": 0.02}
    sa.apply_to_row(low, {"risk_channel": {"penalty": 0.06}}, older_leg_age_ms=None)
    assert low["confidence_score"] == 0.0                                            # clamped
    none = {"confidence_score": 0.4}
    assert sa.apply_to_row(none, None, older_leg_age_ms=None)["risk_unavailable"] is True
    assert none["confidence_score"] == 0.4


# ---------------------------------------------------------------- SEMANTIC_DIRECTION_SOURCE

def _index(usd: float, others: float = 0.0, *, threshold: float = 0.5, confidence: float = 0.8) -> dict:
    """Per-pair currency index as the service builds it: relative bias = base - quote."""
    bias = {"USD": usd, "EUR": others, "GBP": others, "JPY": others, "CHF": others, "INR": others}
    out = {}
    for pair in ("EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF"):
        base, quote = pair.split("/")
        rel = bias[base] - bias[quote]
        label = "bullish" if rel > threshold else "bearish" if rel < -threshold else "neutral"
        out[pair] = {"relative_bias": rel, "label": label, "confidence": confidence}
    return out


def test_dollar_index_falls_moves_each_pair_the_right_way(monkeypatch, tmp_path):
    monkeypatch.setenv(sa.DIRECTION_SOURCE_ENV, "currency_index")
    index = _index(usd=-2.0)
    expected = {"EUR/USD": "risk_on", "GBP/USD": "risk_on", "USD/JPY": "risk_off", "USD/CHF": "risk_off"}
    for pair, want in expected.items():
        # V1 says the opposite of the index for every pair, so the test fails if V1 still decides.
        v1_market = -0.01 if want == "risk_on" else 0.01
        bridge = _bridge(pair, [_analysis(f"{pair} headline {i}", market=v1_market) for i in range(5)], tmp_path)
        client = LiveNewsBiasClient(bridge=bridge, window_minutes=30, direction_lookup=index.get)
        bias = asyncio.run(client.get_bias(pair.replace("/", "")))
        assert bias.direction == want, pair
        assert bias.direction_detail["v1_direction"] != want and bias.direction_detail["source"] == "currency_index"


def test_currency_index_switch_changes_only_the_label(monkeypatch, tmp_path):
    history = [_analysis(f"EUR/USD headline {i}", market=-0.01) for i in range(5)]
    off = LiveNewsBiasClient(bridge=_bridge("EUR/USD", copy.deepcopy(history), tmp_path), window_minutes=30,
                             direction_lookup=_index(usd=-2.0).get)
    before = asyncio.run(off.get_bias("EURUSD")).to_dict()
    assert "direction_detail" not in before                       # recorded keys unchanged while off
    monkeypatch.setenv(sa.DIRECTION_SOURCE_ENV, "currency_index")
    on = LiveNewsBiasClient(bridge=_bridge("EUR/USD", copy.deepcopy(history), tmp_path), window_minutes=30,
                            direction_lookup=_index(usd=-2.0).get)
    after = asyncio.run(on.get_bias("EURUSD")).to_dict()
    for key in ("semantic_score", "confidence_adjustment", "item_count", "regime"):
        assert after[key] == before[key], key
    assert (before["direction"], after["direction"]) == ("risk_off", "risk_on")


def test_currency_index_neutral_low_confidence_or_missing_gives_neutral(monkeypatch, tmp_path):
    monkeypatch.setenv(sa.DIRECTION_SOURCE_ENV, "currency_index")
    history = [_analysis(f"EUR/USD headline {i}", market=0.01) for i in range(5)]
    for lookup in (_index(usd=-0.1).get, _index(usd=-2.0, confidence=0.1).get, None):
        client = LiveNewsBiasClient(bridge=_bridge("EUR/USD", copy.deepcopy(history), tmp_path),
                                    window_minutes=30, direction_lookup=lookup)
        assert asyncio.run(client.get_bias("EURUSD")).direction == "neutral"


# ---------------------------------------------------------------- SEMANTIC_FX_RELEVANCE_FILTER

FX_HISTORY = [
    _analysis("Fed's Powell says the dollar faces more rate cuts", market=0.01),
    _analysis("ECB holds rates as euro area inflation cools", market=0.01),
    _analysis("Czech koruna firms as CNB signals a pause", market=-0.01),
    _analysis("Sensex, Nifty close higher led by HDFC Bank and Infosys", market=-0.01),
]


def test_fx_filter_drops_untagged_headlines_from_the_window(monkeypatch, tmp_path):
    off = _bridge("EUR/USD", copy.deepcopy(FX_HISTORY), tmp_path).get_window_bias("EUR/USD", 30)
    assert off["item_count"] == 4 and "fx_filter_dropped" not in off
    monkeypatch.setenv(sa.FX_FILTER_ENV, "1")
    on = _bridge("EUR/USD", copy.deepcopy(FX_HISTORY), tmp_path).get_window_bias("EUR/USD", 30)
    assert on["item_count"] == 2 and on["fx_filter_dropped"] == 2
    kept = {d["headline"] for d in on["top_drivers"]}
    assert not any("koruna" in h or "Sensex" in h for h in kept)


def test_fx_filter_on_an_empty_window_still_reports_its_count(monkeypatch, tmp_path):
    monkeypatch.setenv(sa.FX_FILTER_ENV, "1")
    out = _bridge("EUR/USD", copy.deepcopy(FX_HISTORY[2:]), tmp_path).get_window_bias("EUR/USD", 30)
    assert out["item_count"] == 0 and out["fx_filter_dropped"] == 2
