"""BasisEvent -> SemanticEngine raw-opp envelope adapter (backend/core/basis/semantic_adapter.py)."""

from datetime import date, datetime, timezone

import pytest

from backend.core.arbitrage.arbitrage_engine import ArbitrageOpportunity, ArbitrageType
from backend.core.basis import BasisEvent
from backend.core.basis.semantic_adapter import basis_event_to_raw_msg


def _ev(**kw):
    base = dict(
        leg_pair="onshore_offshore", buy_leg="onshore", sell_leg="offshore",
        buy_source_id="dhan", sell_source_id="cme", basis_pips=6.4,
        comparison_basis="futures", target_expiry=date(2026, 9, 28),
        carry_adjustment_pips=0.3, buy_quote_ts=1.0, sell_quote_ts=1.1,
        ts=datetime(2026, 8, 30, 11, 0, tzinfo=timezone.utc).timestamp(),  # 16:30 IST
    )
    base.update(kw)
    return BasisEvent.new(**base)


def test_envelope_shape_matches_synthetic_contract():
    msg = basis_event_to_raw_msg(_ev())
    assert set(msg) == {"event_id", "timestamp", "opportunity", "raw_score"}
    assert msg["raw_score"] == 6.4

    opp_dict = msg["opportunity"].copy()
    opp_dict.pop("id", None)
    opp_dict["type"] = ArbitrageType(opp_dict["type"])
    opp = ArbitrageOpportunity(**opp_dict)          # engine reconstructs it exactly like this
    assert opp.symbols == ["USDINR"]                # news-bias lookup key
    assert opp.estimated_profit_pips == 6.4
    assert opp.buy_source == "dhan" and opp.sell_source == "cme"


def test_basis_fields_carried_in_details():
    d = basis_event_to_raw_msg(_ev(persistence_class="persistent"))["opportunity"]["details"]
    assert d["kind"] == "basis_event"
    assert d["leg_pair"] == "onshore_offshore"
    assert d["comparison_basis"] == "futures"
    assert d["target_expiry"] == "2026-09-28"
    assert d["persistence_class"] == "persistent"


def test_verdict_and_persistence_drive_confidence():
    lo = basis_event_to_raw_msg(_ev(execution_verdict="unlikely"))["opportunity"]["confidence_score"]
    hi = basis_event_to_raw_msg(
        _ev(execution_verdict="viable", persistence_class="persistent")
    )["opportunity"]["confidence_score"]
    assert 0.0 <= lo < hi <= 1.0


def test_session_label_from_ist_clock():
    morning = basis_event_to_raw_msg(
        _ev(ts=datetime(2026, 8, 30, 5, 0, tzinfo=timezone.utc).timestamp())  # 10:30 IST
    )["opportunity"]["session"]
    assert morning == "TOKYO_LONDON"
