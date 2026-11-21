"""C44 interim on synthetic cloud data only: gate, raw vs adjusted ranking, latency wiring."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from research import run_c44 as c

AFTER = datetime(2026, 12, 5, 6, 0, tzinfo=UTC)
DAYS = (datetime(2026, 11, 24, 10, 0, tzinfo=UTC), datetime(2026, 11, 25, 10, 0, tzinfo=UTC))


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def build(root):
    """Half the crosses vanish at the next poll; those carry a lower adjusted score (planted)."""
    cloud = root / "cloud"
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
            opps.append({"id": f"o{ts}", "timestamp_ms": ts, "type": "cross_source", "symbols": ["EURUSD"],
                         "buy_source": "spot_truefx_eurusd", "sell_source": "spot_kraken_eurusd",
                         "raw_confidence_score": 0.6, "confidence_score": 0.40 if vanish else 0.58,
                         "semantic_snapshot": {"risk_channel": {"penalty": 0.02},
                                               "direction_channel": {"penalty": 0.0},
                                               "window_bias": {"item_count": 3, "confidence_adjustment": 0.0}}})
        opps.append({"id": "legacy", "timestamp_ms": _ms(day), "type": "cross_source", "symbols": ["EURUSD"],
                     "buy_source": "spot_truefx_eurusd", "sell_source": "spot_kraken_eurusd",
                     "confidence_score": 0.6, "semantic_snapshot": {}})           # no raw score: skipped
        d = cloud / "recordings" / day.date().isoformat()
        d.mkdir(parents=True)
        (d / "spot.jsonl").write_text("".join(json.dumps(q) + "\n" for q in quotes), encoding="utf-8")
        (d / "opportunities.jsonl").write_text("".join(json.dumps(o) + "\n" for o in opps), encoding="utf-8")
    (cloud / "predictions").mkdir(parents=True)
    (cloud / "predictions" / "live_predictions.jsonl").write_text("", encoding="utf-8")
    return cloud


def test_gate_refuses_before_the_interim_window_closes(tmp_path):
    with pytest.raises(SystemExit, match="interim window is open"):
        c.run(tmp_path, now=datetime(2026, 12, 4, 20, 0, tzinfo=UTC))


def test_raw_vs_adjusted_ranking_on_planted_data(tmp_path):
    report = c.run(build(tmp_path), now=AFTER, draws=200)
    ranking = report["I1_ranking"]
    assert ranking["rows"] == 80 and ranking["skipped"]["no_raw_confidence_score"] == 2
    auc = ranking["ranking"]["auc"]
    assert auc["detector"] == pytest.approx(0.5) and auc["adjusted"] == pytest.approx(1.0)
    assert ranking["ranking"]["ci95"]["adjusted_minus_detector"][0] > 0
    assert {"plus_risk", "plus_risk_direction", "plus_window"} <= set(auc)
    assert ranking["not_still_crossed"] == 40
    assert report["I2_latency"]["rows"] == 0 and report["I2_latency"]["observed_offset_min"] is None
