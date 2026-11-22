"""C45 on synthetic data only: ingest (fake BigQuery client), freeze, clock mask, T2-clock, end to end."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from research import gkg_ingest
from research import run_c45 as c


# ---------------------------------------------------------------- fake BigQuery
class _Rows(list):
    def __init__(self, items, frame=None):
        super().__init__(items)
        self._frame = frame

    def to_dataframe(self, **_):
        return self._frame


class FakeClient:
    def __init__(self, headlines: pd.DataFrame, counts: dict[date, int]):
        self.headlines, self.counts = headlines, counts

    def list_rows(self, table):
        if table.endswith(gkg_ingest.COUNTS_TABLE):
            return _Rows([{"day": d, "n": n} for d, n in self.counts.items()])
        return _Rows([], self.headlines)


def _headlines():
    rows = []
    for i, month in enumerate(c.months()[:2]):
        for k in range(5):
            ts = datetime(month.year, month.month, 5 + k, 14, 0, tzinfo=UTC)
            rows.append({"month": month, "ts": ts, "url": f"https://x.example/{i}-{k}", "domain": "x.example",
                         "title": f"Dollar rises as Fed speaks {i}-{k}"})
    return pd.DataFrame(rows)


def test_months_cover_the_pre_registered_period():
    m = c.months()
    assert len(m) == 18 and m[0] == date(2025, 5, 1) and m[-1] == date(2026, 10, 1)


def test_ingest_and_freeze_with_a_fake_client(tmp_path):
    counts = {date(2025, 4, 1) + timedelta(days=i): 1000 for i in range(80)}
    counts[date(2025, 6, 15)] = 0                                   # an outage day
    shards, counts_out = tmp_path / "shards", tmp_path / "counts.json"
    report = c.ingest(FakeClient(_headlines(), counts), "proj", shards, counts_out)
    assert report["daily_count_days"] == 80 and report["months"] == 18 and report["events_written"] == 10
    freeze = c.write_freeze(shards, tmp_path / "freeze.json", counts_out)
    assert freeze["windows_done"] == 18 and freeze["events"] == 10 and len(freeze["sha256"]) == 64
    assert "2025-06-15" in freeze["missing_windows"]
    with pytest.raises(c.C45Error, match="exists"):
        c.write_freeze(shards, tmp_path / "freeze.json", counts_out)
    events, loaded = c.load_frozen_events(tmp_path / "freeze.json", shards)
    assert len(events) == 10 and loaded["sha256"] == freeze["sha256"]
    first = sorted(shards.glob("*.parquet"))[0]
    frame = pd.read_parquet(first)
    frame.iloc[:1].to_parquet(first, index=False)                    # tamper with a frozen shard
    with pytest.raises(c.C45Error, match="hash mismatch"):
        c.load_frozen_events(tmp_path / "freeze.json", shards)


# ---------------------------------------------------------------- clock mask
CLOCKS = {"USD": {"zone": "America/New_York", "clocks": ["08:30"]},
          "EUR": {"zone": "Europe/Berlin", "clocks": ["10:00"]}}


def test_clock_instants_follow_local_time_and_weekdays():
    inst = c.clock_instants(CLOCKS, date(2025, 7, 7), date(2025, 7, 7))       # a Monday, summer
    usd = [datetime.fromtimestamp(t, tz=UTC) for t in inst["USD"]]
    assert datetime(2025, 7, 7, 12, 30, tzinfo=UTC) in usd                     # 08:30 EDT
    assert all(d.weekday() < 5 for d in usd)                                   # Sunday 6 Jul has none
    winter = c.clock_instants(CLOCKS, date(2025, 12, 1), date(2025, 12, 1))
    assert datetime(2025, 12, 1, 13, 30, tzinfo=UTC).timestamp() in winter["USD"]


def test_clock_hit_window_matches_the_release_flag_window():
    inst = c.clock_instants(CLOCKS, date(2025, 7, 7), date(2025, 7, 7))
    at = lambda h, m: datetime(2025, 7, 7, h, m, tzinfo=UTC).timestamp()     # noqa: E731
    times = np.array([at(12, 30), at(13, 0), at(13, 30), at(11, 30)])
    from research.run_window30 import release_flags
    hit = release_flags(inst, "EUR/USD", times, 60)
    # window (t - 45, t + 60]: 12:30 clock is inside for 12:30 (11:45..13:30), 13:00 (12:15..14:00),
    # not for 13:30 (12:45..14:30); EUR 10:00 CEST = 08:00 UTC is outside all; 11:30 (10:45..12:30] includes 12:30.
    assert hit.tolist() == [True, True, False, True]
    assert release_flags(inst, "USD/JPY", times, 60).tolist() == [True, True, False, True]
    assert not release_flags({"JPY": []}, "GBP/JPY", times, 60).any()


# ---------------------------------------------------------------- T2-clock
def _panel(beta: float, seed: int = 1, n_dates: int = 120):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_dates):
        for pair in ("EUR/USD", "GBP/USD"):
            for k in range(10):
                activity = int(rng.integers(0, 12))
                abn = np.exp(beta * np.log1p(activity) + rng.normal(0, 0.5) + rng.normal(0, 0.2) * (d % 3))
                rows.append({"pair": pair, "utc_date": f"d{d:03d}", "horizon": 60, "activity": activity,
                             "abnormal": abn, "clock_hit": k == 0, "release": False, "bias": np.nan,
                             "ret": rng.normal(), "caution": 0.0, "month": "2025-05"})
    return pd.DataFrame(rows)


def test_t2_clock_finds_a_planted_effect_and_not_a_null():
    planted = c.run_clock_tests(_panel(0.10), with_bias=False)
    assert planted["T2_clock"]["h60"]["beta"] == pytest.approx(0.10, abs=0.03)
    assert planted["decision"]["replicated"] is True
    null = c.run_clock_tests(_panel(0.0, seed=7), with_bias=False)
    assert null["decision"]["replicated"] is False
    assert planted["coverage"]["clock_clear_rows"] == planted["coverage"]["signal_rows"] * 9 // 10


def test_clock_rows_are_excluded_from_t2():
    panel = _panel(0.10)
    panel.loc[panel["clock_hit"], "abnormal"] = 1e6                 # would dominate if they were used
    out = c.run_clock_tests(panel, with_bias=False)
    assert out["T2_clock"]["h60"]["beta"] == pytest.approx(0.10, abs=0.03)


# ---------------------------------------------------------------- end to end
def test_evaluate_end_to_end_with_planted_activity_effect():
    start, end = date(2025, 5, 5), date(2025, 8, 29)
    rng = np.random.default_rng(3)
    events = []
    t = datetime(2025, 5, 4, tzinfo=UTC).timestamp()
    while t < datetime(2025, 8, 30, tzinfo=UTC).timestamp():
        burst = rng.random() < 0.15
        for _ in range(int(rng.integers(3, 9)) if burst else int(rng.random() < 0.3)):
            events.append({"event_id": f"e{len(events)}", "timestamp": pd.Timestamp(t + rng.integers(0, 1800), unit="s", tz="UTC"),
                           "currencies": "USD"})
        t += 1800
    events = pd.DataFrame(events)
    canonical = set(events["event_id"])

    def returns_fn(times):
        out = {}
        from research.run_window30 import build_pair_news, window_signal
        frame = events.assign(polarity=0.0, confidence=1.0)
        news = build_pair_news(frame)
        for h in (30, 60):
            out[h] = {}
            for pair, pn in news.items():
                _, activity = window_signal(pn, times)
                out[h][pair] = rng.normal(0, 1e-4, len(times)) * (1 + 0.6 * np.log1p(activity))
        return out

    report = c.evaluate(events, canonical, None, [], start, end, returns_fn, CLOCKS)
    assert report["decision"]["replicated"] is True
    assert report["T2_clock"]["h60"]["beta"] > 0
    assert report["reported_only"] == {"T1_T3": "FinBERT scores not available for this sample"}
    assert report["coverage"]["clock_clear_rows"] < report["coverage"]["signal_rows"]


def test_committed_clock_set_is_the_pre_registered_one():
    clocks = c.load_clocks()
    assert set(clocks) == {"USD", "EUR", "GBP", "JPY", "CHF"}
    assert "08:30" in clocks["USD"]["clocks"] and "07:00" in clocks["GBP"]["clocks"]
