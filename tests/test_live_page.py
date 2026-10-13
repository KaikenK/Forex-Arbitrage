"""The /live monitor page is served and polls /live/status."""

from fastapi.testclient import TestClient

from backend.server.main import app


def test_live_page_is_served():
    resp = TestClient(app).get("/live")      # no lifespan: page route only
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert 'fetch("/live/status"' in resp.text


def test_live_status_explains_when_live_mode_is_off():
    body = TestClient(app).get("/live/status").json()
    assert "LIVE_USDINR_EXCHANGES" in body["error"]
