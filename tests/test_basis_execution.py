"""BasisExecutionFilter — order-book walk, slippage, verdict (T2.3)."""

from datetime import date

import pytest

from backend.core.basis import BasisEvent, BasisExecutionFilter
from backend.core.basis.basis_execution import slippage_pips, _assumed_profile


def _ev(basis_pips=8.0, buy_leg="onshore", sell_leg="offshore", **kw):
    base = dict(
        leg_pair="onshore_offshore", buy_leg=buy_leg, sell_leg=sell_leg,
        buy_source_id="upstox", sell_source_id="cme", basis_pips=basis_pips,
        comparison_basis="futures", target_expiry=date(2026, 9, 28),
        carry_adjustment_pips=0.3, buy_quote_ts=1.0, sell_quote_ts=1.1,
    )
    base.update(kw)
    return BasisEvent.new(**base)


# a deep book: 5 levels, each 2000 lots ($2M) — target $100k fills at the touch
_DEEP = {
    "bids": [{"price": 95.25 - i * 0.0025, "qty": 2000} for i in range(5)],
    "asks": [{"price": 95.26 + i * 0.0025, "qty": 2000} for i in range(5)],
}
# a thin book: 5 lots per level ($5k) — $100k blows through all of it
_THIN = {
    "bids": [{"price": 95.25 - i * 0.01, "qty": 5} for i in range(5)],
    "asks": [{"price": 95.26 + i * 0.01, "qty": 5} for i in range(5)],
}


def test_slippage_zero_when_touch_covers_target():
    prof = [(0.0, 5_000_000.0), (1.0, 8_000_000.0)]
    assert slippage_pips(prof, 100_000.0, thin_book_penalty=2.0) == 0.0


def test_slippage_weighted_average_across_levels():
    # 100k target; 60k at 0 pips, 40k at 2 pips -> 0.8 pips weighted
    prof = [(0.0, 60_000.0), (2.0, 200_000.0)]
    assert slippage_pips(prof, 100_000.0, thin_book_penalty=2.0) == pytest.approx(0.8)


def test_thin_book_is_penalised():
    prof = [(0.0, 10_000.0), (1.0, 20_000.0)]     # only 20k available, want 100k
    s = slippage_pips(prof, 100_000.0, thin_book_penalty=2.0)
    assert s > 1.0                                 # gap charged at worst offset x2


def test_deep_real_book_gives_viable():
    f = BasisExecutionFilter()
    a = f.assess(_ev(basis_pips=8.0), {"onshore": _DEEP})   # sell leg (offshore) assumed
    assert a.buy_slippage_pips == pytest.approx(0.5)        # touch covers $100k -> only the floor
    assert a.assumed_depth is True                          # offshore leg had no book
    assert a.execution_verdict == "viable"
    assert a.net_basis_pips == pytest.approx(8.0 - a.expected_slippage_pips, abs=1e-6)


def test_thin_real_book_erodes_the_verdict():
    f = BasisExecutionFilter()
    deep = f.assess(_ev(basis_pips=3.0), {"onshore": _DEEP})
    thin = f.assess(_ev(basis_pips=3.0), {"onshore": _THIN})
    assert thin.expected_slippage_pips > deep.expected_slippage_pips
    assert thin.net_basis_pips < deep.net_basis_pips


def test_negative_net_is_unlikely():
    f = BasisExecutionFilter()
    a = f.assess(_ev(basis_pips=2.0), {"onshore": _THIN})   # small basis, thin book
    assert a.execution_verdict == "unlikely"
    assert a.net_basis_pips <= 0


def test_offshore_staleness_haircut_applies():
    f = BasisExecutionFilter()
    fresh = f.assess(_ev(basis_pips=6.0), {"onshore": _DEEP}, staleness_ms={"offshore": 0})
    stale = f.assess(_ev(basis_pips=6.0), {"onshore": _DEEP}, staleness_ms={"offshore": 600_000})
    assert stale.staleness_haircut_pips == pytest.approx(0.5 * 10)   # 10 min * 0.5 pips/min
    assert stale.net_basis_pips < fresh.net_basis_pips


def test_stamp_writes_fields_onto_event():
    f = BasisExecutionFilter()
    ev = _ev(basis_pips=8.0)
    f.stamp(ev, {"onshore": _DEEP})
    assert ev.execution_verdict in ("viable", "risky", "unlikely")
    assert ev.assumed_depth is True
    assert ev.expected_slippage_pips >= 0
    ev.validate()                                            # schema still valid


def test_all_assumed_when_no_books():
    f = BasisExecutionFilter()
    a = f.assess(_ev(basis_pips=10.0), {})
    assert a.assumed_depth is True
    # both legs on the assumed profile -> some slippage, still a real verdict
    assert a.execution_verdict in ("viable", "risky", "unlikely")
