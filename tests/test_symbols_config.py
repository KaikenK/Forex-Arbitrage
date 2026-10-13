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


def test_server_reports_what_it_runs_and_what_it_skipped(monkeypatch):
    monkeypatch.setenv("NEWS_STREAM_ENABLE", "0")
    from backend.server import main
    monkeypatch.setattr(main, "_reload_env", lambda: None)
    monkeypatch.setattr(main, "UNSUPPORTED_SYMBOLS", ["EURINR"])
    with TestClient(main.app) as c:
        body = c.get("/symbols").json()
        assert body["symbols"] == ["USDINR"] and body["unsupported"] == ["EURINR"]
        status = c.get("/feeds/mode").json()
        assert status["symbols"] == ["USDINR"] and status["unsupported_symbols"] == ["EURINR"]
