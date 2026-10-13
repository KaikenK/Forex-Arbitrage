"""Runtime feed switching through /feeds/mode, against the real app lifespan."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.core.data_sources.exchange_contracts import FutureContract
from backend.core.data_sources.quote_hub import Quote
from backend.core.data_sources.tick_recorder import QuoteRecorder


def _record_session(root):
    rec = QuoteRecorder(root)
    for ex, key in (("NSE", "NCD_FO|1"), ("BSE", "BCD_FO|2")):
        rec.register_contract(FutureContract(ex, key, "USDINR FUT 28 OCT 26", date(2026, 10, 28),
                                             1, 1000.0, provider="upstox"))
    for i in range(5):
        t = 1_800_000_000_000 + i * 1000
        rec.on_poll("upstox", {"NCD_FO|1": Quote(bid=95.445, ask=95.4475, recv_ts_ms=t),
                               "BCD_FO|2": Quote(bid=95.44, ask=95.455, recv_ts_ms=t)})
    return rec.session_dir.name


@pytest.fixture
def client(tmp_path, monkeypatch):
    for var in ("UPSTOX_ACCESS_TOKEN", "KITE_API_KEY", "KITE_ACCESS_TOKEN", "ARBEX_LIVE_REPLAY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ARBEX_RECORD_DIR", str(tmp_path))
    monkeypatch.setenv("NEWS_STREAM_ENABLE", "0")
    from backend.server import main
    monkeypatch.setattr(main, "_reload_env", lambda: None)   # ignore a developer's real .env
    session = _record_session(tmp_path)
    with TestClient(main.app) as c:
        yield c, session


def test_switch_synthetic_live_replay_and_back(client):
    c, session = client
    status = c.get("/feeds/mode").json()
    assert status["mode"] == "synthetic"
    assert len(status["sources"]) == 6
    assert status["available"]["live"]["available"] is False
    assert "UPSTOX_ACCESS_TOKEN" in status["available"]["live"]["reason"]
    assert status["available"]["replay"]["sessions"] == [session]

    # live without keys is refused and synthetic keeps running
    r = c.post("/feeds/mode", json={"mode": "live"})
    assert r.status_code == 409
    assert r.json()["mode"] == "synthetic" and len(r.json()["sources"]) == 6

    # replay a recorded session
    r = c.post("/feeds/mode", json={"mode": "replay", "session": session})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "replay" and body["replay_session"] == session
    assert body["sources"] == ["replay_upstox_bse_usdinr", "replay_upstox_nse_usdinr"]
    assert body["data_mode"] == "LIVE_USDINR_EXCHANGES"
    assert c.get("/live/status").json()["mode"] == "replay"

    # back to synthetic: six synthetic sources again, live-only endpoint switches off
    r = c.post("/feeds/mode", json={"mode": "synthetic"})
    assert r.status_code == 200
    assert len(r.json()["sources"]) == 6 and r.json()["data_mode"] == "SYNTHETIC_USDINR_ONLY"
    assert "error" in c.get("/live/status").json()


def test_replay_rejects_paths_and_unknown_sessions(client):
    c, _ = client
    for bad in ("../../etc", "2099-01-01"):
        r = c.post("/feeds/mode", json={"mode": "replay", "session": bad})
        assert r.status_code == 400
    assert c.post("/feeds/mode", json={"mode": "turbo"}).status_code == 400
    assert c.get("/feeds/mode").json()["mode"] == "synthetic"


def test_keys_added_to_env_file_make_live_available_without_restart(tmp_path, monkeypatch):
    import os
    from backend.server import main
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    monkeypatch.setattr(main, "_ENV_FILE", env_file)
    try:
        assert main.feed_availability()["live"]["available"] is False
        env_file.write_text("UPSTOX_ACCESS_TOKEN=attached-while-running\n")
        live = main.feed_availability()["live"]
        assert live["providers"] == ["upstox"]
    finally:
        os.environ.pop("UPSTOX_ACCESS_TOKEN", None)
