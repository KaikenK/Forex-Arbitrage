"""DEVIATION 4 ingestion rules on synthetic BigQuery rows (no network, no credentials)."""
from __future__ import annotations

import json
from datetime import date

import pandas as pd

from research import gkg_ingest as gkg


def _rows():
    return pd.DataFrame([
        {"ts": "2024-01-15 12:30:00+00:00", "url": "https://a.example/1", "domain": "a.example",
         "title": "Dollar rallies as Fed signals rate hike &amp; inflation fears"},
        {"ts": "2024-01-15 12:45:00+00:00", "url": "https://a.example/1", "domain": "a.example",      # same URL later
         "title": "Dollar rallies as Fed signals rate hike &amp; inflation fears"},
        {"ts": "2024-01-15 13:00:00+00:00", "url": "https://b.example/2", "domain": "b.example",
         "title": "Pound shop opens new store in Leeds"},                                          # no FX context
        {"ts": "2024-01-15 13:15:00+00:00", "url": "https://c.example/3", "domain": "c.example", "title": None},
        {"ts": "2024-01-15 13:30:00+00:00", "url": "https://d.example/4", "domain": "d.example",
         "title": "ECB holds rates as euro steadies"},
    ])


def test_rows_become_tagged_events_with_doc_style_ids():
    events = gkg.rows_to_events(_rows())
    assert [e.url for e in events] == ["https://a.example/1", "https://d.example/4"]
    first = events[0]
    assert first.headline == "Dollar rallies as Fed signals rate hike & inflation fears"        # unescaped
    assert first.timestamp.isoformat() == "2024-01-15T12:30:00+00:00"                          # earliest kept
    assert first.event_id == gkg.event_id_for("https://a.example/1") and len(first.event_id) == 16
    assert "USD" in first.currencies and first.source == "gdelt_gkg"
    assert events[1].currencies == ("EUR",)


def test_months_and_shard_names():
    months = gkg.months()
    assert len(months) == 88 and months[0] == date(2018, 1, 1) and months[-1] == date(2025, 4, 1)
    assert gkg.shard_name(date(2019, 12, 1)) == "20191201T000000"


def test_ingest_writes_resumable_monthly_shards(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(client, project, month):
        calls.append(month)
        return _rows()

    monkeypatch.setattr(gkg, "fetch_month", fake_fetch)
    wanted = [date(2024, 1, 1), date(2024, 2, 1)]
    report = gkg.ingest(None, "proj", tmp_path, wanted)
    assert report["events_written"] == 4 and len(calls) == 2
    meta = json.loads((tmp_path / "20240101T000000.done").read_text(encoding="utf-8"))
    assert meta["count"] == 2 and meta["completed_at"]
    again = gkg.ingest(None, "proj", tmp_path, wanted)
    assert again["skipped_done"] == 2 and len(calls) == 2


def test_gap_rule_uses_the_trailing_median():
    counts = {f"2024-03-{d:02d}": 400_000 for d in range(1, 31)}
    counts["2024-03-10"] = 30_000           # 7.5% of the median: gap
    counts["2024-03-11"] = 60_000           # 15%: not a gap
    gaps = gkg.gap_days(counts, date(2024, 3, 1), date(2024, 3, 31))
    assert "2024-03-10" in gaps and "2024-03-11" not in gaps and "2024-03-31" in gaps   # 31st absent


def test_fetch_month_reads_the_table_once_and_splits_by_month(monkeypatch):
    reads = []

    def fake_table(client, project, table=gkg.TABLE):
        reads.append(table)
        frame = _rows()
        frame["month"] = ["2024-01-01"] * 4 + ["2024-02-01"]
        return frame

    monkeypatch.setattr(gkg, "fetch_table", fake_table)
    client = object()
    jan = gkg.fetch_month(client, "proj", date(2024, 1, 1), _cache={})
    cache: dict = {}
    assert len(gkg.fetch_month(client, "proj", date(2024, 1, 1), _cache=cache)) == 4
    assert len(gkg.fetch_month(client, "proj", date(2024, 2, 1), _cache=cache)) == 1
    assert gkg.fetch_month(client, "proj", date(2024, 3, 1), _cache=cache).empty
    assert len(reads) == 2 and len(jan) == 4                     # one read per cache, not per month
