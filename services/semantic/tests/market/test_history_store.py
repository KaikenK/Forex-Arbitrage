"""Engine update C38: the history store reads only its tail and writes on a background thread."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from market.history_store import JsonlAnalysisHistoryStore, article_analysis_from_payload
from market import history_store as hs


def _payload(i: int) -> dict:
    produced = datetime(2026, 11, 4, tzinfo=UTC) + timedelta(minutes=i)
    return {"event_id": f"e{i}", "sentiment_score": 0.1, "market_score": 0.0, "affected_symbols": ["EUR/USD"],
            "confidence": 0.5, "evidence": [], "risk_flags": [], "produced_at": produced.isoformat(),
            "key_drivers": [], "methodology": {}, "trading_signal": "HOLD", "signal_rationale": "x" * 300}


def test_tail_load_matches_the_newest_records_of_a_full_load(tmp_path, monkeypatch):
    monkeypatch.setattr(hs, "TAIL_BLOCK_BYTES", 1024)              # many blocks, lines cut at edges
    path = tmp_path / "history.jsonl"
    path.write_text("\n".join(json.dumps(_payload(i)) for i in range(300)) + "\nnot json\n", encoding="utf-8")
    store = JsonlAnalysisHistoryStore(path)
    tail, full = store.load(50), store.load()
    assert [a.event_id for a in tail[:50]] == [a.event_id for a in full[:50]] == [f"e{i}" for i in range(299, 249, -1)]


def test_appends_are_written_by_the_background_thread_and_visible_to_load(tmp_path):
    store = JsonlAnalysisHistoryStore(tmp_path / "sub" / "history.jsonl")
    for i in range(20):
        store.append(article_analysis_from_payload(_payload(i)))
    assert [a.event_id for a in store.load(5)][:3] == ["e19", "e18", "e17"]     # load() flushes first
    assert len((tmp_path / "sub" / "history.jsonl").read_text(encoding="utf-8").splitlines()) == 20
