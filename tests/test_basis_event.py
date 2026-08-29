"""Tests for the frozen BasisEvent schema (docs/SPEC.md section 7)."""

from datetime import date

import pytest

from backend.core.basis import BasisEvent, SCHEMA_VERSION


def _ev(**kw):
    base = dict(
        leg_pair="onshore_offshore", buy_leg="onshore", sell_leg="offshore",
        buy_source_id="kite", sell_source_id="cme", basis_pips=4.2,
        comparison_basis="futures", target_expiry=date(2026, 9, 28),
        carry_adjustment_pips=0.4, buy_quote_ts=1.0, sell_quote_ts=1.1,
    )
    base.update(kw)
    return BasisEvent.new(**base)


def test_new_fills_identity_and_validates():
    e = _ev()
    assert e.event_id and e.ts > 0 and e.symbol == "USDINR"
    assert e.schema_version == SCHEMA_VERSION
    assert e.target_expiry == "2026-09-28"      # date coerced to iso string
    assert e.persistence_class == "ephemeral"   # default for a raw event


def test_rejects_bad_enums():
    with pytest.raises(ValueError):
        _ev(leg_pair="nope")
    with pytest.raises(ValueError):
        _ev(execution_verdict="maybe")
    with pytest.raises(ValueError):
        _ev(comparison_basis="spot")


def test_stream_roundtrip():
    e = _ev(basis_pips=7.5, persistence_class="persistent")
    fields = e.to_stream_fields()
    assert set(fields) == {"schema", "payload"}
    back = BasisEvent.from_stream_fields(fields)
    assert back.basis_pips == 7.5
    assert back.persistence_class == "persistent"
    assert back.event_id == e.event_id
