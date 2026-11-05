"""/live marks crosses against a leg the detector ignores; / goes to the live monitor in live mode."""
from fastapi.testclient import TestClient

from backend.server import main
from backend.server.main import _mark_stale, app


def test_cross_with_an_unhealthy_leg_is_marked_stale():
    legs = [
        {"id": "spot_kraken_eurusd", "healthy": False, "age_ms": 240712, "suppressed_reason": "stale (240712 ms)"},
        {"id": "spot_truefx_eurusd", "healthy": True, "age_ms": 1100, "suppressed_reason": None},
        {"id": "spot_truefx_gbpusd", "healthy": True, "age_ms": 900, "suppressed_reason": None},
        {"id": "spot_kraken_gbpusd", "healthy": True, "age_ms": 800, "suppressed_reason": None},
    ]
    rows = [
        {"symbol": "EURUSD", "buy": "spot_kraken_eurusd", "sell": "spot_truefx_eurusd", "executable_pips": 3.7},
        {"symbol": "GBPUSD", "buy": "spot_kraken_gbpusd", "sell": "spot_truefx_gbpusd", "executable_pips": -0.3},
    ]
    out = _mark_stale(rows, legs)
    assert out[0]["stale"] is True
    assert out[0]["stale_legs"] == [{"id": "spot_kraken_eurusd", "age_ms": 240712, "reason": "stale (240712 ms)"}]
    assert out[1]["stale"] is False and out[1]["stale_legs"] == []


def test_live_page_labels_stale_crosses():
    text = TestClient(app).get("/live").text
    assert "not an opportunity" in text and "r.stale" in text


def test_root_redirects_to_live_monitor_in_live_mode(monkeypatch):
    monkeypatch.setattr(main, "is_live_exchange_mode", lambda: True)
    resp = TestClient(app).get("/", follow_redirects=False)
    assert resp.status_code == 307 and resp.headers["location"] == "/live"


def test_legacy_terminal_still_served():
    resp = TestClient(app).get("/legacy")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/html")
