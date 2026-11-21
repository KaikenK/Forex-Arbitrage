"""C43 shadow analysis on synthetic recordings and shadow rows only."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from research import run_c43 as c43

AFTER = datetime(2026, 11, 18, 0, 0, tzinfo=UTC)
DAYS = (datetime(2026, 11, 5, 15, 0, tzinfo=UTC), datetime(2026, 11, 6, 10, 0, tzinfo=UTC))


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def _shadow(oid, ts, *, adjusted, filtered, v1="neutral", ci="neutral", dropped=3, written=1.0):
    return {"id": oid, "timestamp_ms": ts, "pair": "EUR/USD", "confidence_score": 0.6,
            "adjusted_confidence_score": adjusted, "window_direction_v1": v1,
            "window_direction_currency_index": ci, "written_at": written,
            "window_filtered": {"item_count": round(filtered * 5), "activity_factor": filtered,
                                "fx_filter_dropped": dropped}}


def build(root, *, shadow_split=True):
    """Two days. Half the crosses vanish at the next poll: those carry a low adjusted score and a busy
    filtered window, so (a) and (b) have a planted signal; the unfiltered window is uninformative."""
    rec, sh_a, sh_b = root / "rec", root / "shadow_app", root / "shadow_real"
    shadow_rows = []
    for day in DAYS:
        quotes, opps = [], []
        for k in range(40):
            t = day + timedelta(minutes=k)
            vanish = k % 2 == 0
            ts = _ms(t)
            quotes.append({"provider": "truefx", "exchange": "TRUEFX", "symbol": "EURUSD", "bid": 1.1000,
                           "ask": 1.1001, "recv_ts_ms": ts + 1000})
            quotes.append({"provider": "kraken", "exchange": "KRAKEN", "symbol": "EURUSD",
                           "bid": 1.1000 if vanish else 1.1003, "ask": 1.1005, "recv_ts_ms": ts + 1100})
            oid = f"cross_source_EURUSD_{ts}"
            opps.append({"id": oid, "timestamp_ms": ts, "type": "cross_source", "symbols": ["EURUSD"],
                         "buy_source": "spot_truefx_eurusd", "sell_source": "spot_kraken_eurusd",
                         "confidence_score": 0.6,
                         "semantic_snapshot": {"window_bias": {"item_count": 10}}})
            shadow_rows.append(_shadow(oid, ts, adjusted=0.40 if vanish else 0.58,
                                       filtered=0.8 if vanish else 0.2,
                                       v1="risk_on" if k % 4 == 0 else "neutral", ci="neutral"))
        d = rec / day.date().isoformat()
        d.mkdir(parents=True)
        (d / "spot.jsonl").write_text("".join(json.dumps(q) + "\n" for q in quotes), encoding="utf-8")
        (d / "opportunities.jsonl").write_text("".join(json.dumps(o) + "\n" for o in opps), encoding="utf-8")
    first, second = (shadow_rows[:50], shadow_rows[50:]) if shadow_split else (shadow_rows, [])
    for folder, rows in ((sh_a, first), (sh_b, second)):
        for day in DAYS:
            p = folder / day.date().isoformat()
            p.mkdir(parents=True, exist_ok=True)
            day_rows = [r for r in rows if datetime.fromtimestamp(r["timestamp_ms"] / 1000, tz=UTC).date() == day.date()]
            (p / c43.SHADOW_FILE).write_text("".join(json.dumps(r) + "\n" for r in day_rows), encoding="utf-8")
    return rec, [sh_a, sh_b]


def test_gate_refuses_before_18_nov(tmp_path):
    with pytest.raises(SystemExit, match="still open"):
        c43.run(tmp_path, [], now=datetime(2026, 11, 10, tzinfo=UTC))


def test_union_of_folders_keeps_one_row_per_id_earliest_written(tmp_path):
    a, b = tmp_path / "a" / "2026-11-05", tmp_path / "b" / "2026-11-05"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / c43.SHADOW_FILE).write_text(json.dumps(_shadow("x", 1, adjusted=0.5, filtered=0.2, written=5.0)) + "\n")
    (b / c43.SHADOW_FILE).write_text(json.dumps(_shadow("x", 1, adjusted=0.1, filtered=0.2, written=2.0)) + "\n"
                                     + json.dumps(_shadow("y", 2, adjusted=0.3, filtered=0.2)) + "\n")
    rows, counts = c43.load_shadow([tmp_path / "a", tmp_path / "b"])
    assert set(rows) == {"x", "y"} and rows["x"]["adjusted_confidence_score"] == 0.1
    assert counts["rows_read"] == 3 and counts["duplicate_ids"] == 1


def test_end_to_end_planted_signal(tmp_path):
    rec, dirs = build(tmp_path)
    report = c43.run(rec, dirs, now=AFTER, draws=200)
    assert report["shadow_ids"] == 80 and report["evaluable_rows"] == 80
    a = report["a_adjusted_vs_detector"]
    assert a["auc"]["detector"] == pytest.approx(0.5)          # constant detector score
    assert a["auc"]["adjusted"] == pytest.approx(1.0)          # planted
    assert a["ci95"]["adjusted_minus_detector"][0] > 0
    b = report["b_filtered_vs_unfiltered_activity"]
    assert b["auc_not_still_crossed"]["filtered"] == pytest.approx(1.0)
    assert b["auc_not_still_crossed"]["unfiltered"] == pytest.approx(0.5)
    c = report["c_direction_agreement"]
    assert c["n_with_both"] == 80 and c["agree"] == 60 and c["n_both_directional"] == 0
    d = report["d_filter_removal"]
    assert d["items_removed"] == 80 * 3 and 0 < d["pooled_share_removed"] < 1


def test_rows_outside_window_or_unjoined_are_skipped(tmp_path):
    rec, dirs = build(tmp_path, shadow_split=False)
    extra = dirs[1] / "2026-11-05" / c43.SHADOW_FILE
    extra.write_text(json.dumps(_shadow("not_recorded", _ms(DAYS[0]), adjusted=0.5, filtered=0.2)) + "\n")
    report = c43.run(rec, dirs, now=AFTER, draws=50)
    assert report["skipped"]["no_matching_opportunity"] == 1
    assert report["evaluable_rows"] == 80
