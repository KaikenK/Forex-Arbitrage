"""The /live monitor page is served and polls /live/status."""

from fastapi.testclient import TestClient

from backend.server.main import app


def test_live_page_is_served():
    resp = TestClient(app).get("/live")      # no lifespan: page route only
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    # same-origin by default; ?api= points a preview of the file at a running backend
    assert "fetch(`${API}/live/status`" in resp.text
    assert '<link rel="stylesheet" href="/static/arbex-theme.css">' in resp.text


def test_shared_theme_is_served_for_the_live_page():
    resp = TestClient(app).get("/static/arbex-theme.css")
    assert resp.status_code == 200 and "--color-canvas" in resp.text


def test_live_status_explains_when_live_mode_is_off():
    body = TestClient(app).get("/live/status").json()
    assert "LIVE_USDINR_EXCHANGES" in body["error"]
