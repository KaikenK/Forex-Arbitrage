"""Tests for BasisArbitrageEngine + BasisPersistenceTracker."""

from datetime import date

import pytest

from backend.config import BasisDetectionConfig
from backend.core.basis import (
    BasisArbitrageEngine,
    BasisPersistenceTracker,
    BasisEvent,
    score_basis_event,
)
from backend.core.normalization import InstrumentNormalizer

TSTAR = date(2026, 9, 28)


def _fwds(onshore_mid, offshore_mid, otc_mid=None, spread=0.02):
    n = InstrumentNormalizer(TSTAR, 0.019)
    out = {}
    for leg, mid, kind in (("onshore", onshore_mid, "future"),
                           ("offshore", offshore_mid, "future"),
                           ("otc", otc_mid, "spot")):
        if mid is None:
            continue
        out[leg] = n.to_common_forward(
            mid - spread / 2, mid + spread / 2, leg=leg, instrument_kind=kind,
            source_id=leg, quote_ts=1000.0,
            quote_expiry=TSTAR if kind == "future" else None,
            quote_time=None if kind == "future" else __import__("datetime").datetime(2026, 9, 1),
        )
    return out


def test_no_event_below_threshold():
    eng = BasisArbitrageEngine(BasisDetectionConfig(min_basis_threshold_pips=2.0))
    # onshore 86.500/.510, offshore 86.505/.515 -> executable basis < 0
    ev = eng.detect(_fwds(86.505, 86.510), now_ts=1000.0, target_expiry=TSTAR)
    assert ev == []


def test_event_direction_and_fields():
    eng = BasisArbitrageEngine(BasisDetectionConfig(min_basis_threshold_pips=2.0))
    # onshore mid 86.560, offshore mid 86.500 -> buy offshore, sell onshore
    evs = eng.detect(_fwds(86.560, 86.500), now_ts=1234.0, target_expiry=TSTAR)
    assert len(evs) == 1
    e = evs[0]
    assert e.leg_pair == "onshore_offshore"
    assert e.buy_leg == "offshore" and e.sell_leg == "onshore"
    assert e.basis_pips >= 2.0
    assert e.cadence == "tick" and e.raw_score == e.basis_pips
    assert e.ts == 1234.0


def test_multiple_pairs():
    eng = BasisArbitrageEngine(BasisDetectionConfig(min_basis_threshold_pips=2.0))
    evs = eng.detect(_fwds(86.560, 86.500, otc_mid=86.520),
                     now_ts=1000.0, target_expiry=TSTAR)
    pairs = {e.leg_pair for e in evs}
    assert "onshore_offshore" in pairs and "onshore_otc" in pairs


def _mk(**kw):
    base = dict(leg_pair="onshore_offshore", buy_leg="offshore", sell_leg="onshore",
                buy_source_id="a", sell_source_id="b", basis_pips=5.0,
                comparison_basis="futures", target_expiry=TSTAR,
                carry_adjustment_pips=0.0, buy_quote_ts=1.0, sell_quote_ts=1.0)
    base.update(kw)
    return BasisEvent.new(**base)


class TestScoreBasisEvent:
    def test_bigger_basis_scores_higher(self):
        assert score_basis_event(_mk(basis_pips=10)) > score_basis_event(_mk(basis_pips=3))

    def test_persistence_and_verdict_help(self):
        base = score_basis_event(_mk())
        assert score_basis_event(_mk(persistence_class="persistent")) > base
        assert score_basis_event(_mk(execution_verdict="viable")) > base
        assert score_basis_event(_mk(execution_verdict="unlikely")) < base

    def test_stale_offshore_penalised(self):
        fresh = score_basis_event(_mk(offshore_staleness_ms=0))
        stale = score_basis_event(_mk(offshore_staleness_ms=1_800_000))
        assert stale == fresh - 20 or stale < fresh

    def test_never_negative(self):
        assert score_basis_event(_mk(basis_pips=2.0, execution_verdict="unlikely",
                                     carry_adjustment_pips=50)) >= 0.0


class TestPersistenceTrackerCount:
    def test_count_mode_progression(self):
        tr = BasisPersistenceTracker(BasisDetectionConfig(), mode="count")
        ev = BasisEvent.new(leg_pair="onshore_offshore", buy_leg="offshore",
                            sell_leg="onshore", buy_source_id="a", sell_source_id="b",
                            basis_pips=5, comparison_basis="futures",
                            target_expiry=TSTAR, carry_adjustment_pips=0,
                            buy_quote_ts=1, sell_quote_ts=1)
        seen = [tr.observe(ev) for _ in range(5)]
        assert seen == ["ephemeral", "flickering", "flickering", "persistent", "persistent"]

    def test_different_direction_tracked_separately(self):
        tr = BasisPersistenceTracker(BasisDetectionConfig(), mode="count")
        a = dict(leg_pair="onshore_offshore", sell_leg="x", buy_source_id="a",
                 sell_source_id="b", basis_pips=5, comparison_basis="futures",
                 target_expiry=TSTAR, carry_adjustment_pips=0, buy_quote_ts=1,
                 sell_quote_ts=1)
        tr.observe(BasisEvent.new(buy_leg="offshore", **a))
        cls = tr.observe(BasisEvent.new(buy_leg="onshore", **a))
        assert cls == "ephemeral"        # first sighting of the other direction


class TestPersistenceTrackerDuration:
    def test_duration_mode(self):
        cfg = BasisDetectionConfig(persistence_ephemeral_max_s=5,
                                   persistence_flickering_max_s=60)
        tr = BasisPersistenceTracker(cfg, mode="duration", gap_tolerance_s=3.0)
        base = dict(leg_pair="onshore_otc", buy_leg="otc", sell_leg="onshore",
                    buy_source_id="a", sell_source_id="b", basis_pips=4,
                    comparison_basis="futures", target_expiry=TSTAR,
                    carry_adjustment_pips=0)
        for t in (0.0, 2.0, 4.0):
            tr.observe(BasisEvent.new(ts=t, buy_quote_ts=t, sell_quote_ts=t, **base))
        # cumulative active ~4s -> still ephemeral (< 5s)
        assert tr.classify("onshore_otc|otc") == "ephemeral"
        for t in (6.0, 8.0, 10.0, 40.0, 62.0):
            tr.observe(BasisEvent.new(ts=t, buy_quote_ts=t, sell_quote_ts=t, **base))
        assert tr.classify("onshore_otc|otc") in ("flickering", "persistent")
