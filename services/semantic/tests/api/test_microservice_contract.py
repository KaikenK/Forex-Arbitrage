"""The microservice contract the Arbex backend relies on (services/semantic/README.md).

- every route answers both bare and under /v1
- /health carries a service block: api_version, uptime, served artifact identity
- /currency-index is a declared slot that answers 501 until a provider is wired in
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from api.main import build_app
from api.main import strip_version_prefix
from tests.api.test_routes import _build_service
from tests.api.test_routes import _request

SERVICE_ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ARTIFACT = SERVICE_ROOT / "artifacts" / "usdinr_targeted_expanded" / "reaction_model_artifact.json"


def test_version_prefix_is_stripped_only_as_a_whole_segment() -> None:
    assert strip_version_prefix("/v1/analyze") == "/analyze"
    assert strip_version_prefix("/v1/sentiment/live") == "/sentiment/live"
    assert strip_version_prefix("/analyze") == "/analyze"
    assert strip_version_prefix("/v1") == "/"
    assert strip_version_prefix("/v10/analyze") == "/v10/analyze"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/health", "/v1/health"])
async def test_health_reports_the_service_block(path: str) -> None:
    app = build_app(_build_service(), service_info=lambda: {"api_version": "v1", "uptime_seconds": 1.5})
    status, payload = await _request(app, "GET", path)
    assert status == 200
    assert payload["service"] == {"api_version": "v1", "uptime_seconds": 1.5}
    assert payload["health"]["status"]


@pytest.mark.asyncio
async def test_versioned_and_bare_analyze_agree() -> None:
    body = {"article_text": "Dollar firms after strong US payrolls.", "currency_pair": "EUR/USD"}
    bare_status, bare = await _request(build_app(_build_service()), "POST", "/analyze", body)
    v1_status, v1 = await _request(build_app(_build_service()), "POST", "/v1/analyze", body)
    assert bare_status == v1_status == 200
    assert bare["result"]["market_score"] == v1["result"]["market_score"]


@pytest.mark.asyncio
async def test_currency_index_is_a_501_slot_until_wired() -> None:
    status, payload = await _request(build_app(_build_service()), "GET", "/v1/currency-index")
    assert status == 501
    assert payload["code"] == "NOT_IMPLEMENTED"


@pytest.mark.asyncio
async def test_currency_index_serves_a_wired_provider() -> None:
    def provider(query: dict[str, str]) -> dict[str, object]:
        return {"as_of": "2026-10-24", "currencies": {"USD": 0.1}, "pairs": {"EUR/USD": -0.1},
                "market_component": None, "method": "stub", "query": query}

    app = build_app(_build_service(), currency_index_provider=provider)
    status, payload = await _request(app, "GET", "/v1/currency-index?date=2026-10-24")
    assert status == 200
    assert payload["pairs"] == {"EUR/USD": -0.1} and payload["query"] == {"date": "2026-10-24"}


def _load_serve():
    spec = importlib.util.spec_from_file_location("semantic_serve", SERVICE_ROOT / "serve.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_shipped_artifact_is_weights_only_and_identified_in_health() -> None:
    serve = _load_serve()
    info = serve.build_service_info(SHIPPED_ARTIFACT, {"currency_index": False})()
    assert info["model_loaded"] is True and info["api_version"] == "v1"
    artifact = info["artifact"]
    assert artifact["weights_only"] is True and artifact["model_family"] == "linear"
    assert artifact["available_horizons"] == [60, 240, 1440]
    assert artifact["bytes"] < 200_000, "the shipped model must stay small"
    assert len(artifact["sha256"]) == 16
    assert info["uptime_seconds"] >= 0


def test_missing_artifact_is_reported_not_raised(tmp_path: Path) -> None:
    info = _load_serve().build_service_info(tmp_path / "missing.json", {})()
    assert info["model_loaded"] is False
