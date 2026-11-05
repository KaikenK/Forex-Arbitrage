"""C43 shadow engine: off by default, writes apart from the recordings, never modifies its inputs."""
from __future__ import annotations

import copy
import json
from datetime import UTC, datetime

import pytest

from tests.test_currency_bias_channels import StubClient, _bias, _pair, _score
from tests.test_phase2_flags import FX_HISTORY, _bridge
from tests.test_semantic_engine import FixedNewsBiasProvider, make_opportunity

from backend.core import semantic_shadow as sh
from backend.core.semantic_engine import SemanticEngine

STAMP_MS = int(datetime(2026, 11, 6, 9, 30, tzinfo=UTC).timestamp() * 1000)


@pytest.fixture(autouse=True)
def _off(monkeypatch):
    for name in (sh.SHADOW_ENV, sh.SHADOW_DIR_ENV, "SEMANTIC_FX_RELEVANCE_FILTER",
                 "SEMANTIC_DIRECTION_SOURCE", "ARBEX_APPLY_SEMANTIC"):
        monkeypatch.delenv(name, raising=False)


def test_off_by_default_and_root_outside_the_recordings(monkeypatch, tmp_path):
    assert sh.shadow_enabled() is False
    monkeypatch.setenv(sh.SHADOW_ENV, "1")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert sh.shadow_enabled() is True
    assert sh.shadow_root() == tmp_path / "arbex" / "shadow"            # not .../arbex/recordings
    monkeypatch.setenv(sh.SHADOW_DIR_ENV, str(tmp_path / "x"))
    assert sh.shadow_root() == tmp_path / "x"


def test_pair_inputs_filter_the_window_while_the_live_switch_stays_off(tmp_path):
    bridge = _bridge("EUR/USD", copy.deepcopy(FX_HISTORY), tmp_path)
    payload = {"relative_bias": 2.0, "label": "bullish", "confidence": 0.8}
    inputs = sh.pair_shadow(bridge, payload, "EUR/USD", 30, clock=lambda: 100.0)
    assert inputs["window_filtered"]["item_count"] == 2 and inputs["window_filtered"]["fx_filter_dropped"] == 2
    assert inputs["window_filtered"]["activity_factor"] == pytest.approx(0.4)
    assert inputs["currency_index"]["direction"] == "risk_on"
    live = bridge.get_window_bias("EUR/USD", 30)                         # the live window is untouched
    assert live["item_count"] == 4 and "fx_filter_dropped" not in live


def test_shadow_row_copies_its_inputs_and_adjusts_confidence():
    row = {"id": "cross_EURUSD_1", "type": "cross_source", "timestamp_ms": STAMP_MS, "confidence_score": 0.5,
           "buy_source": "spot_truefx_eurusd", "sell_source": "spot_kraken_eurusd"}
    snapshot = {"applied": False, "risk_channel": {"penalty": 0.06}, "direction_channel": {"penalty": 0.03},
                "window_bias": {"direction": "risk_off", "item_count": 4}}
    before_row, before_snapshot = copy.deepcopy(row), copy.deepcopy(snapshot)
    cache = {"computed_at": 90.0, "currency_index": {"direction": "risk_on"},
             "window_filtered": {"item_count": 2, "activity_factor": 0.4, "fx_filter_dropped": 2}}
    out = sh.shadow_row(row, snapshot, "EUR/USD", cache, older_leg_age_ms=3_000, clock=lambda: 100.0)
    assert row == before_row and snapshot == before_snapshot             # recorder's objects unchanged
    assert out["confidence_score"] == 0.5
    assert out["adjusted_confidence_score"] == pytest.approx(0.5 - 0.06 - 0.025, abs=1e-3)
    assert (out["window_direction_v1"], out["window_direction_currency_index"]) == ("risk_off", "risk_on")
    assert out["window_filtered"]["item_count"] == 2 and out["shadow_inputs_age_seconds"] == 10.0
    missing = sh.shadow_row(row, None, "EUR/USD", None, older_leg_age_ms=None, clock=lambda: 100.0)
    assert missing["adjusted_confidence_score"] == 0.5 and missing["risk_unavailable"] is True
    assert missing["window_filtered"] is None and missing["shadow_inputs_age_seconds"] is None


def test_writer_files_rows_by_the_opportunitys_utc_date(tmp_path):
    writer = sh.ShadowWriter(tmp_path)
    path = writer.write({"id": "a", "timestamp_ms": STAMP_MS})
    writer.write({"id": "b", "timestamp_ms": STAMP_MS + 1})
    assert path == tmp_path / "2026-11-06" / sh.SHADOW_FILE
    assert [json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines()] == ["a", "b"]
    assert writer.rows_written == 2


@pytest.mark.asyncio
async def test_dashboard_engine_can_apply_risk_without_direction():
    payload = _pair(bias=-4.0, label="bearish", confidence=0.9, expected=40.0, ratio=5.0)
    both = SemanticEngine(news_bias_provider=FixedNewsBiasProvider(_bias()), currency_bias=True,
                          currency_bias_client=StubClient(payload))
    risk_only = SemanticEngine(news_bias_provider=FixedNewsBiasProvider(_bias()), currency_bias=True,
                               currency_bias_client=StubClient(payload), apply_direction=False)
    a = await _score(both, make_opportunity(confidence=0.82))
    b = await _score(risk_only, make_opportunity(confidence=0.82))
    risk = b["semantic_channels"]["risk"]["penalty"]
    direction = b["semantic_channels"]["direction"]["penalty"]
    assert direction > 0
    assert b["opportunity"]["confidence_score"] == pytest.approx(round(0.82 - 0.04 - risk, 3))
    assert a["opportunity"]["confidence_score"] == pytest.approx(round(0.82 - 0.04 - risk - direction, 3))
    assert "direction not applied" in b["ranking_reason"] and "direction not applied" not in a["ranking_reason"]
