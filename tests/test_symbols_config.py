"""ARBEX_SYMBOLS: the traded pairs are a setting, validated by the instrument registry."""

import pytest
from fastapi.testclient import TestClient

from backend.config import RESEARCH_SYMBOL, configured_symbols
from backend.core.instruments import UnknownInstrumentError


def test_unset_means_the_research_symbol(monkeypatch):
    monkeypatch.delenv("ARBEX_SYMBOLS", raising=False)
    assert configured_symbols() == [RESEARCH_SYMBOL]


def test_symbols_are_normalized_and_deduplicated(monkeypatch):
    monkeypatch.setenv("ARBEX_SYMBOLS", "usd/inr, EUR_INR ,USDINR")
    assert configured_symbols() == ["USDINR", "EURINR"]


def test_a_typo_fails_loudly(monkeypatch):
    monkeypatch.setenv("ARBEX_SYMBOLS", "USDINR,GOLD")
    with pytest.raises(UnknownInstrumentError):
        configured_symbols()


def test_presets_expand_to_the_exchange_listed_pairs(monkeypatch):
    monkeypatch.setenv("ARBEX_SYMBOLS", "all")
    assert configured_symbols() == ["USDINR", "EURINR", "GBPINR", "JPYINR", "EURUSD", "GBPUSD", "USDJPY"]
    monkeypatch.setenv("ARBEX_SYMBOLS", "inr")
    assert configured_symbols() == ["USDINR", "EURINR", "GBPINR", "JPYINR"]


def test_each_feed_runs_the_pairs_it_can_and_reports_the_rest(tmp_path, monkeypatch):
    """Synthetic models every requested pair; a replay only has what was recorded."""
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from test_feed_switching import _record_session            # a USDINR-only recording
    for var in ("UPSTOX_ACCESS_TOKEN", "KITE_API_KEY", "KITE_ACCESS_TOKEN", "ARBEX_LIVE_REPLAY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ARBEX_SYMBOLS", "USDINR,EURINR,AUDUSD")
    monkeypatch.setenv("ARBEX_RECORD_DIR", str(tmp_path))
    monkeypatch.setenv("NEWS_STREAM_ENABLE", "0")
    from backend.server import main
    monkeypatch.setattr(main, "_reload_env", lambda: None)
    session = _record_session(tmp_path)
    with TestClient(main.app) as c:
        status = c.get("/feeds/mode").json()
        assert status["symbols"] == ["USDINR", "EURINR", "AUDUSD"] and status["unsupported_symbols"] == []
        assert len(status["sources"]) == 18                       # 6 session x provider feeds per pair
        assert "bloomberg_tokyo_eurinr" in status["sources"]
        assert status["instruments"]["EURINR"]["display"] == "EUR/INR"

        r = c.post("/feeds/mode", json={"mode": "replay", "session": session})
        assert r.status_code == 200, r.text
        body = c.get("/symbols").json()
        assert body["symbols"] == ["USDINR"] and body["unsupported"] == ["EURINR", "AUDUSD"]
        live = c.get("/live/status").json()
        assert {leg["symbol"] for leg in live["legs"]} == {"USDINR"}
        assert all(row["symbol"] == "USDINR" for row in live["cross_spreads"])

        c.post("/feeds/mode", json={"mode": "synthetic"})
        assert c.get("/symbols").json()["symbols"] == ["USDINR", "EURINR", "AUDUSD"]
