"""C40 on synthetic recordings only: gate, windows, controls, gaps, statistics, end to end."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from research import run_c40 as c40

AFTER = datetime(2026, 11, 18, 0, 0, tzinfo=UTC)
RELEASE = datetime(2026, 11, 10, 13, 30, tzinfo=UTC)          # a Tuesday
CONTROLS = (RELEASE - timedelta(weeks=1), RELEASE + timedelta(weeks=1))


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def _write_archive(path, rows):
    path.mkdir(parents=True, exist_ok=True)
    (path / "ff_calendar_2026-W46.json").write_text(json.dumps(rows), encoding="utf-8")


def _calendar_rows(extra=()):
    return [{"title": "Non-Farm Employment Change", "country": "USD", "impact": "High",
             "date": RELEASE.isoformat()},
            {"title": "Bank Holiday", "country": "USD", "impact": "High", "date": "2026-11-11T00:00:00+00:00"},
            {"title": "Retail Sales", "country": "USD", "impact": "Low", "date": "2026-11-02T10:00:00+00:00"},
            {"title": "Trade Balance", "country": "EUR", "impact": "Low", "date": "2026-11-17T20:00:00+00:00"},
            *extra]


def test_gate_refuses_before_18_nov(tmp_path):
    with pytest.raises(SystemExit, match="still open"):
        c40.run(tmp_path, None, None, now=datetime(2026, 11, 6, tzinfo=UTC))


def test_load_releases_keeps_high_impact_timed_study_rows_and_spans(tmp_path):
    _write_archive(tmp_path, _calendar_rows([{"title": "x", "country": "AUD", "impact": "High",
                                               "date": RELEASE.isoformat()}]))
    releases, spans = c40.load_releases(tmp_path)
    assert [(r["currency"], r["time"]) for r in releases] == [("USD", RELEASE)]     # all-day, low, AUD dropped
    assert spans == [(datetime(2026, 11, 2, tzinfo=UTC), datetime(2026, 11, 18, tzinfo=UTC))]


def test_activity_factor_rebuilt_from_item_count():
    assert c40.activity_factor({"window_bias": {"item_count": 3}}) == pytest.approx(0.6)
    assert c40.activity_factor({"window_bias": {"item_count": 9}}) == 1.0
    assert c40.activity_factor({"window_bias": {"item_count": 0}}) == 0.0
    assert c40.activity_factor({"window_bias": None}) is None
    assert c40.activity_factor(None) is None


def test_controls_skip_nearby_releases_uncovered_weeks_and_gaps():
    span = [(datetime(2026, 11, 2, tzinfo=UTC), datetime(2026, 11, 18, tzinfo=UTC))]
    near = CONTROLS[0] + timedelta(minutes=45)                 # EUR release 45 min after a control time
    releases = [{"currency": "USD", "time": RELEASE}, {"currency": "EUR", "time": near}]
    recorded = (_ms(datetime(2026, 11, 1, tzinfo=UTC)), _ms(datetime(2026, 11, 18, tzinfo=UTC)))
    rel, ctl, counts = c40.release_windows(releases, ["EURUSD"], span, [], recorded,
                                           window=c40.PRIMARY_WINDOW, end=c40.END_UTC)
    anchors = {c["anchor"] for c in ctl}
    assert CONTROLS[0] not in anchors                          # within 60 min of the EUR release
    assert CONTROLS[1] in anchors
    assert RELEASE - timedelta(weeks=2) not in anchors        # outside the calendar span
    assert counts["control_near_release"] >= 1 and counts["control_unusable"] >= 1
    gap = [(_ms(CONTROLS[1]), _ms(CONTROLS[1] + timedelta(minutes=1)))]
    _, ctl_gap, _ = c40.release_windows(releases, ["EURUSD"], span, gap, recorded,
                                        window=c40.PRIMARY_WINDOW, end=c40.END_UTC)
    assert CONTROLS[1] not in {c["anchor"] for c in ctl_gap}
    assert {r["anchor"] for r in rel} == {RELEASE, near}       # EURUSD maps both currencies


def test_find_gaps_and_next_poll_guard():
    base = _ms(RELEASE)
    quotes = {("kraken", "KRAKEN", "EURUSD"): ([base, base + 1000, base + 20_000], [{}, {}, {}])}
    gaps, recorded = c40.find_gaps(quotes)
    assert gaps == [(base + 1000, base + 20_000)] and recorded == (base, base + 20_000)
    opp = {"timestamp_ms": base + 500, "buy_source": "spot_truefx_eurusd", "sell_source": "spot_kraken_eurusd"}
    rows, skipped = c40.build_rows([opp], quotes, end=c40.END_UTC, gaps=gaps, one_sided="drop")
    assert rows == [] and skipped == {"next_poll_after_gap": 1}


def _rows_for(n_days, *, release_crossed, control_crossed, per_window=2):
    rows, rel, ctl = [], [], []
    start = datetime(2026, 10, 20, 13, 30, tzinfo=UTC)
    for d in range(n_days):
        anchor = start + timedelta(days=d)
        for kind, crossed, shift in (("rel", release_crossed, 0), ("ctl", control_crossed, 3)):
            t = anchor + timedelta(hours=shift)
            window = {"pair": "EURUSD", "anchor": t, "start": _ms(t - timedelta(minutes=5)),
                      "end": _ms(t + timedelta(minutes=15))}
            (rel if kind == "rel" else ctl).append(window)
            for k in range(per_window):
                rows.append({"ts": _ms(t) + k * 1000, "day": t.date().isoformat(), "pair": "EURUSD",
                             "still_crossed": int(crossed),
                             "edge_pips": 0.5})
    return rows, rel, ctl


def test_release_test_recovers_planted_effect_and_sign():
    rows, rel, ctl = _rows_for(35, release_crossed=0, control_crossed=1)
    out = c40.release_test(rows, rel, ctl, draws=200)
    assert out["difference"] == pytest.approx(-1.0)
    assert out["p_one_sided"] < 0.01
    assert out["rate_ratio"] == pytest.approx(1.0)


def test_release_test_not_computable_below_30_windows():
    rows, rel, ctl = _rows_for(20, release_crossed=0, control_crossed=1)
    out = c40.release_test(rows, rel, ctl, draws=50)
    assert "not_computable" in out


def test_auc_test_planted_effect_and_threshold():
    rng = np.random.default_rng(1)
    rows = []
    for i in range(300):
        vanished = i % 2
        rows.append({"day": f"2026-11-{1 + i % 15:02d}", "still_crossed": 1 - vanished,
                     "activity_factor": float(rng.uniform(0.5, 1.0) if vanished else rng.uniform(0.0, 0.6))})
    out = c40.auc_test(rows, "activity_factor", draws=200)
    assert out["auc_not_still_crossed"] > 0.8 and out["p_one_sided"] < 0.01
    assert "not_computable" in c40.auc_test(rows[:150], "activity_factor", draws=10)


def test_benjamini_hochberg_matches_hand_values():
    q = c40.benjamini_hochberg({"a": 0.01, "b": 0.04, "c": 0.03, "d": None})
    assert q["a"] == pytest.approx(0.03) and q["b"] == pytest.approx(0.04) and q["c"] == pytest.approx(0.04)
    assert q["d"] is None


def _write_day(root, day, quotes, opps):
    d = root / day
    d.mkdir(parents=True, exist_ok=True)
    with (d / "truefx.jsonl").open("a", encoding="utf-8") as fh:
        fh.writelines(json.dumps(q) + "\n" for q in quotes if q["provider"] == "truefx")
    with (d / "kraken.jsonl").open("a", encoding="utf-8") as fh:
        fh.writelines(json.dumps(q) + "\n" for q in quotes if q["provider"] == "kraken")
    with (d / "opportunities.jsonl").open("a", encoding="utf-8") as fh:
        fh.writelines(json.dumps(o) + "\n" for o in opps)


def synthetic_recordings(root):
    """Three 40-minute blocks: the release (crosses vanish, busy news window) and two controls."""
    for anchor, crossed, items in ((RELEASE, False, 5), (CONTROLS[0], True, 0), (CONTROLS[1], True, 0)):
        quotes, opps = [], []
        t0 = anchor - timedelta(minutes=20)
        for s in range(0, 40 * 60, 2):
            ts = _ms(t0) + s * 1000
            quotes.append({"provider": "truefx", "exchange": "TRUEFX", "symbol": "EURUSD", "bid": 1.1000,
                           "ask": 1.1001, "recv_ts_ms": ts, "exchange_ts_ms": ts - 200})
            quotes.append({"provider": "kraken", "exchange": "KRAKEN", "symbol": "EURUSD",
                           "bid": 1.1002 if crossed else 1.1000, "ask": 1.1004, "recv_ts_ms": ts + 100,
                           "exchange_ts_ms": ts - 900})
        for k in range(12):
            ts = _ms(anchor) + k * 60_000 + 500
            opps.append({"timestamp_ms": ts, "type": "cross_source", "symbols": ["EURUSD"],
                         "buy_source": "spot_truefx_eurusd", "sell_source": "spot_kraken_eurusd",
                         "confidence_score": 0.5,
                         "semantic_snapshot": {"window_bias": {"item_count": items},
                                               "risk_channel": {"penalty": 0.0},
                                               "direction_channel": {"penalty": 0.0}}})
        _write_day(root, anchor.date().isoformat(), quotes, opps)


def test_end_to_end_on_synthetic_recordings(tmp_path, monkeypatch):
    rec, cal = tmp_path / "recordings", tmp_path / "archive"
    synthetic_recordings(rec)
    _write_archive(cal, _calendar_rows())
    monkeypatch.setattr(c40, "MIN_RELEASE_WINDOWS", 1)
    monkeypatch.setattr(c40, "MIN_OUTCOMES", 10)
    report = c40.run(rec, cal, None, now=AFTER, draws=100)
    main = report["main"]
    assert main["rows"] == 36 and main["pairs"] == ["EURUSD"]
    t1 = main["tests"]["C40-1"]
    assert t1["release_windows_with_opportunity"] == 1 and t1["control_windows"] == 2
    assert t1["still_crossed_share_release"] == 0.0 and t1["still_crossed_share_control"] == 1.0
    assert main["tests"]["C40-3"]["auc_not_still_crossed"] == 1.0
    assert main["decision"]["news_as_risk_live"] in {"SUPPORTED", "NOT SUPPORTED"}
    assert report["sensitivity_minus_resends_C39"]["unavailable"].startswith("prediction log not found")
    assert report["recorder_gaps"] == 2                        # between the three blocks
